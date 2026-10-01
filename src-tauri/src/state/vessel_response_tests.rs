use super::*;
use std::{future::Future, pin::Pin, task::Poll};
use tokio::sync::oneshot;

// No credential-store, filesystem, network, sleeps, or scheduler timing dependencies.
fn core() -> Core {
    let base = Url::parse("https://first.example/").unwrap();
    let mut snapshot = Snapshot::initial(Some(base.to_string()));
    snapshot.authentication.status = AuthenticationState::Authenticated;
    snapshot.tenants.active = Some(TenantView {
        id: "tenant-a".into(),
        name: "A".into(),
    });
    Core {
        state: Arc::new(Mutex::new(RuntimeState {
            snapshot,
            base_url: Some(base),
            auth_config: None,
            credentials: Some(Credentials {
                provider_refresh: Zeroizing::new("test-refresh".into()),
                quayt_session: Zeroizing::new("test-session".into()),
            }),
            session_version: Some(1),
            session_generation: 1,
            vessel_calls: VesselCallStatus {
                status: "ready",
                calls: vec![call()],
                selected: Some(call()),
                summary: summary(),
            },
            vessel_calls_generation: 0,
        })),
        operation: Arc::new(Mutex::new(())),
        login_cancel: Arc::new(Mutex::new(None)),
        api: ServiceApi::new(),
        settings_path: PathBuf::new(),
        client_instance_id: uuid::Uuid::nil(),
    }
}

fn call() -> VesselCall {
    serde_json::from_value(json!({
        "id": "0f9426e8-98e7-4f17-aacf-d184fe9ad69e",
        "tenant_id": "tenant-a", "vessel_name": "OLD-TENANT-VESSEL",
        "imo_number": "1234567", "agent_name": null, "berth": null,
        "eta": "2030-02-03T12:00:00+00:00", "etd": null, "status": "expected"
    }))
    .unwrap()
}

fn summary() -> VesselCallSummary {
    VesselCallSummary {
        total: 37,
        expected: 37,
        ..Default::default()
    }
}

async fn assert_pending<T>(mut future: Pin<&mut impl Future<Output = T>>) {
    std::future::poll_fn(|cx| {
        assert!(
            future.as_mut().poll(cx).is_pending(),
            "response must still be delayed"
        );
        Poll::Ready(())
    })
    .await;
}

#[derive(Clone, Copy, Debug)]
enum Transition {
    Tenant,
    Logout,
    Reconfigure,
    Session,
    NewRequest,
}

async fn transition(core: &Core, transition: Transition) {
    core.mutate(|state| {
        if matches!(transition, Transition::NewRequest) {
            vessel_request_context(state, true).unwrap();
            state.snapshot.issue = Some(issue(SafeError::TenantRejected));
            return;
        }
        // The same invalidation function used by tenant selection, logout, and configuration.
        begin_workspace_transition(state);
        match transition {
            Transition::Tenant => {
                state.snapshot.tenants.active = Some(TenantView {
                    id: "tenant-b".into(),
                    name: "B".into(),
                });
                state.session_version = Some(2);
            }
            Transition::Logout => {
                state.snapshot.authentication.status = AuthenticationState::LogoutPending
            }
            Transition::Reconfigure => {
                state.base_url = Some(Url::parse("https://second.example/").unwrap())
            }
            Transition::Session | Transition::NewRequest => {
                state.snapshot.tenants.active = Some(TenantView {
                    id: "tenant-a".into(),
                    name: "A".into(),
                });
            }
        }
        state.snapshot.issue = Some(issue(SafeError::TenantRejected));
    })
    .await;
    let snapshot = core.gui_snapshot().await;
    assert_eq!(snapshot["vesselCalls"]["calls"], json!([]));
    assert_eq!(snapshot["vesselCalls"]["selected"], Value::Null);
    assert_eq!(snapshot["vesselCalls"]["summary"]["total"], 0);
}

#[tokio::test]
async fn delayed_list_and_summary_success_and_error_cannot_cross_workspace_boundaries() {
    for change in [
        Transition::Tenant,
        Transition::Logout,
        Transition::Reconfigure,
        Transition::Session,
        Transition::NewRequest,
    ] {
        for delay_summary in [false, true] {
            for fail in [false, true] {
                let core = core();
                let binding = {
                    let mut state = core.state.lock().await;
                    vessel_request_context(&mut state, true).unwrap().2
                };
                assert_eq!(core.gui_snapshot().await["vesselCalls"]["calls"], json!([]));
                let (summary_tx, summary_rx) = oneshot::channel();
                let (list_tx, list_rx) = oneshot::channel();
                // One endpoint has completed; the other is held until after the transition.
                let (mut summary_tx, mut list_tx) = (Some(summary_tx), Some(list_tx));
                if delay_summary {
                    list_tx.take().unwrap().send(Ok(vec![call()])).unwrap();
                } else {
                    summary_tx.take().unwrap().send(Ok(summary())).unwrap();
                }
                let response = core.receive_vessel_calls(
                    binding,
                    async { summary_rx.await.unwrap() },
                    async { list_rx.await.unwrap() },
                );
                tokio::pin!(response);
                assert_pending(response.as_mut()).await;
                transition(&core, change).await;
                let before = core.gui_snapshot().await;
                if let Some(tx) = summary_tx {
                    tx.send(if fail {
                        Err(SafeError::SessionRejected)
                    } else {
                        Ok(summary())
                    })
                    .unwrap();
                }
                if let Some(tx) = list_tx {
                    tx.send(if fail {
                        Err(SafeError::SessionRejected)
                    } else {
                        Ok(vec![call()])
                    })
                    .unwrap();
                }
                response.await;
                let after = core.gui_snapshot().await;
                assert_eq!(after["vesselCalls"], before["vesselCalls"], "{change:?}");
                assert_eq!(
                    after["issue"], before["issue"],
                    "stale error for {change:?}"
                );
                assert!(!after.to_string().contains("OLD-TENANT-VESSEL"));
                assert!(core
                    .handle_cli(json!({"cmd":"status"}))
                    .await
                    .resp
                    .get("vesselCalls")
                    .is_none());
            }
        }
    }
}

#[tokio::test]
async fn delayed_detail_success_and_error_cannot_cross_workspace_boundaries() {
    for change in [
        Transition::Tenant,
        Transition::Logout,
        Transition::Reconfigure,
        Transition::Session,
        Transition::NewRequest,
    ] {
        for fail in [false, true] {
            let core = core();
            let binding = {
                let mut state = core.state.lock().await;
                vessel_request_context(&mut state, false).unwrap().2
            };
            let (tx, rx) = oneshot::channel();
            let response = core.receive_vessel_detail(binding, async { rx.await.unwrap() });
            tokio::pin!(response);
            assert_pending(response.as_mut()).await;
            transition(&core, change).await;
            let before = core.gui_snapshot().await;
            tx.send(if fail {
                Err(SafeError::SessionRejected)
            } else {
                Ok(call())
            })
            .unwrap();
            response.await;
            let after = core.gui_snapshot().await;
            assert_eq!(after["vesselCalls"], before["vesselCalls"], "{change:?}");
            assert_eq!(
                after["issue"], before["issue"],
                "stale error for {change:?}"
            );
        }
    }
}

#[tokio::test]
async fn current_responses_commit_and_each_binding_component_is_required() {
    let core = core();
    let binding = {
        let mut state = core.state.lock().await;
        vessel_request_context(&mut state, true).unwrap().2
    };
    core.receive_vessel_calls(binding.clone(), async { Ok(summary()) }, async {
        Ok(vec![call()])
    })
    .await;
    assert_eq!(
        core.gui_snapshot().await["vesselCalls"]["summary"]["total"],
        37
    );
    core.receive_vessel_detail(binding.clone(), async { Ok(call()) })
        .await;
    assert_eq!(
        core.gui_snapshot().await["vesselCalls"]["selected"]["vessel_name"],
        "OLD-TENANT-VESSEL"
    );
    for component in 0..5 {
        let mut stale = binding.clone();
        match component {
            0 => stale.service_url = Url::parse("https://other.example/").unwrap(),
            1 => stale.session_generation += 1,
            2 => stale.session_version = Some(99),
            3 => stale.tenant_id = "other".into(),
            _ => stale.request_generation += 1,
        }
        let before = core.gui_snapshot().await;
        core.receive_vessel_calls(
            stale.clone(),
            async { Err(SafeError::SessionRejected) },
            async { Ok(vec![]) },
        )
        .await;
        core.receive_vessel_detail(stale, async { Err(SafeError::SessionRejected) })
            .await;
        let after = core.gui_snapshot().await;
        assert_eq!(before["vesselCalls"], after["vesselCalls"]);
        assert_eq!(before["issue"], after["issue"]);
    }
}

#[tokio::test]
async fn rejected_gui_requests_and_empty_logout_do_not_reenter_state_mutex() {
    for request in [
        json!({"cmd":"loadVesselCalls"}),
        json!({"cmd":"selectVesselCall", "id":"0f9426e8-98e7-4f17-aacf-d184fe9ad69e"}),
        json!({"cmd":"selectTenant", "tenantId":"0f9426e8-98e7-4f17-aacf-d184fe9ad69e"}),
        json!({"cmd":"logout"}),
    ] {
        let core = core();
        core.finish_signed_out_without_config().await;
        tokio::time::timeout(std::time::Duration::from_secs(1), core.handle_gui(request))
            .await
            .expect("state mutex deadlock");
    }
}

#[tokio::test]
async fn actual_workspace_commands_clear_data_before_waiting_for_an_operation() {
    let core = core();
    let operation = core.operation.lock().await;
    let switch = core.select_tenant("0f9426e8-98e7-4f17-aacf-d184fe9ad69e");
    tokio::pin!(switch);
    assert_pending(switch.as_mut()).await;
    let snapshot = core.gui_snapshot().await;
    assert_eq!(snapshot["tenants"]["active"], Value::Null);
    assert_eq!(snapshot["vesselCalls"]["calls"], json!([]));
    assert_eq!(snapshot["vesselCalls"]["summary"]["total"], 0);
    let logout = core.logout();
    tokio::pin!(logout);
    assert_pending(logout.as_mut()).await;
    assert_eq!(
        core.gui_snapshot().await["authentication"]["status"],
        "logoutPending"
    );
    drop(operation);
    // Drop the pending commands without invoking any HTTP or OS credential operation.
}

#[tokio::test]
async fn reconfiguration_clears_data_even_when_settings_cannot_be_saved() {
    let core = core();
    // The fixture's empty settings path deliberately fails persistence without writing a file.
    assert!(core
        .configure_service("https://second.example/")
        .await
        .is_err());
    let snapshot = core.gui_snapshot().await;
    assert_eq!(snapshot["tenants"]["active"], Value::Null);
    assert_eq!(snapshot["vesselCalls"]["calls"], json!([]));
    assert_eq!(snapshot["vesselCalls"]["selected"], Value::Null);
    assert_eq!(snapshot["vesselCalls"]["summary"]["total"], 0);
}
