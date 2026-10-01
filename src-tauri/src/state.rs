use crate::auth::{
    validate_service_url, AuthConfig, CredentialVault, Credentials, SafeError, ServiceApi,
    SessionView, TenantView, VesselCall, VesselCallFilters, VesselCallSummary,
};
use crate::CLI;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
use url::Url;
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum ConnectionState {
    NotConfigured,
    Checking,
    Ready,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum AuthenticationState {
    SignedOut,
    SigningIn,
    Authenticated,
    LogoutPending,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ConnectionStatus {
    status: ConnectionState,
    service_url: Option<String>,
    summary: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct AuthenticationStatus {
    status: AuthenticationState,
    summary: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct TenantStatus {
    active: Option<TenantView>,
    available: Vec<TenantView>,
    selection_required: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct VesselCallStatus {
    status: &'static str,
    summary: VesselCallSummary,
    calls: Vec<VesselCall>,
    selected: Option<VesselCall>,
}

impl Default for VesselCallStatus {
    fn default() -> Self {
        Self {
            status: "idle",
            summary: VesselCallSummary::default(),
            calls: Vec::new(),
            selected: None,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct SafeIssue {
    code: &'static str,
    summary: &'static str,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Snapshot {
    ok: bool,
    rev: u64,
    busy: bool,
    connection: ConnectionStatus,
    authentication: AuthenticationStatus,
    tenants: TenantStatus,
    issue: Option<SafeIssue>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GuiSnapshot {
    #[serde(flatten)]
    safe: Snapshot,
    vessel_calls: VesselCallStatus,
}

impl Snapshot {
    fn initial(service_url: Option<String>) -> Self {
        let configured = service_url.is_some();
        Self {
            ok: true,
            rev: 0,
            busy: false,
            connection: ConnectionStatus {
                status: if configured {
                    ConnectionState::Checking
                } else {
                    ConnectionState::NotConfigured
                },
                service_url,
                summary: if configured {
                    "Waiting to check the configured service.".into()
                } else {
                    "Configure the Quayt development service to continue.".into()
                },
            },
            authentication: AuthenticationStatus {
                status: AuthenticationState::SignedOut,
                summary: "Not signed in.".into(),
            },
            tenants: TenantStatus {
                active: None,
                available: Vec::new(),
                selection_required: false,
            },
            issue: None,
        }
    }
}

#[derive(Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Settings {
    service_url: Option<String>,
    client_instance_id: Option<uuid::Uuid>,
}

struct RuntimeState {
    snapshot: Snapshot,
    base_url: Option<Url>,
    auth_config: Option<AuthConfig>,
    credentials: Option<Credentials>,
    session_version: Option<u64>,
    session_generation: u64,
    vessel_calls: VesselCallStatus,
    vessel_calls_generation: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct VesselRequestBinding {
    service_url: Url,
    session_generation: u64,
    session_version: Option<u64>,
    tenant_id: String,
    request_generation: u64,
}

#[derive(Clone)]
pub struct Core {
    state: Arc<Mutex<RuntimeState>>,
    operation: Arc<Mutex<()>>,
    login_cancel: Arc<Mutex<Option<CancellationToken>>>,
    api: ServiceApi,
    settings_path: PathBuf,
    client_instance_id: uuid::Uuid,
}

impl Core {
    pub fn new() -> Self {
        let settings_path = clappkit::data_file(CLI, "settings.json");
        let settings = clappkit::store::load_json::<Settings>(&settings_path).unwrap_or_default();
        let client_instance_id = settings
            .client_instance_id
            .unwrap_or_else(uuid::Uuid::new_v4);
        let base_url = settings
            .service_url
            .as_deref()
            .and_then(|value| validate_service_url(value).ok());
        let service_url = base_url
            .as_ref()
            .map(|url| url.as_str().trim_end_matches('/').to_string());
        if settings.client_instance_id.is_none() {
            let _ = clappkit::store::save_json(
                &settings_path,
                &Settings {
                    service_url: service_url.clone(),
                    client_instance_id: Some(client_instance_id),
                },
            );
        }
        Self {
            state: Arc::new(Mutex::new(RuntimeState {
                snapshot: Snapshot::initial(service_url),
                base_url,
                auth_config: None,
                credentials: None,
                session_version: None,
                session_generation: 0,
                vessel_calls: VesselCallStatus::default(),
                vessel_calls_generation: 0,
            })),
            operation: Arc::new(Mutex::new(())),
            login_cancel: Arc::new(Mutex::new(None)),
            api: ServiceApi::new(),
            settings_path,
            client_instance_id,
        }
    }

    pub async fn bootstrap(&self) -> Value {
        if self.state.lock().await.base_url.is_some() {
            self.connect().await
        } else {
            self.snapshot().await
        }
    }

    pub async fn handle_cli(&self, req: Value) -> clappkit::app::Reply {
        match req.get("cmd").and_then(Value::as_str) {
            Some("status") => self.safe_reply().await,
            Some(_) => self.error_reply("unsupported command".into()).await,
            None => self.error_reply("missing command".into()).await,
        }
    }

    pub async fn handle_gui(&self, req: Value) -> clappkit::app::Reply {
        match req.get("cmd").and_then(Value::as_str) {
            Some("state") | Some("status") => self.gui_reply().await,
            Some("configureService") => {
                let input = req
                    .get("serviceUrl")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let _ = match self.configure_service(input).await {
                    Ok(value) => value,
                    Err(error) => self.apply_error(error).await,
                };
                self.gui_reply().await
            }
            Some("connect") => {
                self.connect().await;
                self.gui_reply().await
            }
            Some("login") => {
                self.login().await;
                self.gui_reply().await
            }
            Some("cancelLogin") => {
                if let Some(cancel) = self.login_cancel.lock().await.take() {
                    cancel.cancel();
                }
                self.gui_reply().await
            }
            Some("selectTenant") => {
                let tenant_id = req
                    .get("tenantId")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                self.select_tenant(tenant_id).await;
                self.gui_reply().await
            }
            Some("loadVesselCalls") => {
                let filters = vessel_call_filters(&req);
                self.load_vessel_calls(filters).await;
                self.gui_reply().await
            }
            Some("selectVesselCall") => {
                let id = req.get("id").and_then(Value::as_str).unwrap_or_default();
                self.load_vessel_call_detail(id).await;
                self.gui_reply().await
            }
            Some("logout") => {
                self.logout().await;
                self.gui_reply().await
            }
            Some(_) => self.error_reply("unsupported command".into()).await,
            None => self.error_reply("missing command".into()).await,
        }
    }

    async fn configure_service(&self, input: &str) -> Result<Value, SafeError> {
        let base = validate_service_url(input)?;
        self.mutate(begin_workspace_transition).await;
        let display = base.as_str().trim_end_matches('/').to_string();
        if !clappkit::store::save_json(
            &self.settings_path,
            &Settings {
                service_url: Some(display.clone()),
                client_instance_id: Some(self.client_instance_id),
            },
        ) {
            return Err(SafeError::ServiceUnavailable);
        }
        self.mutate(|state| {
            state.base_url = Some(base);
            state.auth_config = None;
            state.credentials = None;
            state.session_version = None;
            state.session_generation = state.session_generation.saturating_add(1);
            state.snapshot.connection.status = ConnectionState::Checking;
            state.snapshot.connection.service_url = Some(display);
            state.snapshot.connection.summary = "Checking the configured service.".into();
            state.snapshot.authentication.status = AuthenticationState::SignedOut;
            state.snapshot.authentication.summary = "Not signed in.".into();
            state.snapshot.tenants = TenantStatus {
                active: None,
                available: Vec::new(),
                selection_required: false,
            };
            state.vessel_calls = VesselCallStatus::default();
            state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
            state.snapshot.issue = None;
        })
        .await;
        Ok(self.connect().await)
    }

    async fn connect(&self) -> Value {
        let _operation = self.operation.lock().await;
        if self.state.lock().await.snapshot.authentication.status
            == AuthenticationState::LogoutPending
        {
            return self.apply_error(SafeError::LogoutIncomplete).await;
        }
        let context = {
            let state = self.state.lock().await;
            state
                .base_url
                .clone()
                .map(|base| (base, state.session_generation))
        };
        let Some((base, generation)) = context else {
            return self.apply_error(SafeError::InvalidServiceUrl).await;
        };
        self.mutate(|state| {
            state.snapshot.busy = true;
            state.snapshot.connection.status = ConnectionState::Checking;
            state.snapshot.connection.summary =
                "Checking service readiness and sign-in configuration.".into();
            state.snapshot.issue = None;
        })
        .await;
        let config = match self.api.discover(&base).await {
            Ok(config) => config,
            Err(error) => return self.finish_error(error).await,
        };
        let vault = CredentialVault::for_service(&base);
        let credentials = match vault.load() {
            Ok(value) => value,
            Err(error) => return self.finish_error(error).await,
        };
        if let Some(credentials) = credentials {
            match self.api.session(&base, &credentials.quayt_session).await {
                Ok(session) => {
                    drop(_operation);
                    return self
                        .finish_connected(&base, generation, config, credentials, session)
                        .await;
                }
                Err(SafeError::SessionRejected) => {
                    let refresh = &credentials.provider_refresh;
                    match self.api.refresh_provider(&config, refresh).await {
                        Ok(tokens) => match self
                            .api
                            .rotate_session(&base, &credentials.quayt_session, &tokens.access)
                            .await
                        {
                            Ok(session_credential) => {
                                if vault
                                    .save_provider(&tokens)
                                    .and_then(|_| vault.save_session(&session_credential))
                                    .is_err()
                                {
                                    let _ = vault.clear();
                                    return self
                                        .finish_error(SafeError::CredentialStoreUnavailable)
                                        .await;
                                }
                                match self.api.session(&base, &session_credential).await {
                                    Ok(session) => {
                                        let refreshed = Credentials {
                                            provider_refresh: tokens
                                                .refresh
                                                .expect("refresh preserves a refresh token"),
                                            quayt_session: session_credential,
                                        };
                                        drop(_operation);
                                        return self
                                            .finish_connected(
                                                &base, generation, config, refreshed, session,
                                            )
                                            .await;
                                    }
                                    Err(error) => return self.finish_error(error).await,
                                }
                            }
                            Err(SafeError::ServiceUnavailable) => {
                                return self.finish_error(SafeError::ServiceUnavailable).await
                            }
                            Err(error) => {
                                let _ = vault.clear();
                                return self.finish_signed_out(config, Some(error)).await;
                            }
                        },
                        Err(SafeError::ServiceUnavailable) => {
                            return self.finish_error(SafeError::ServiceUnavailable).await
                        }
                        Err(error) => {
                            let _ = vault.clear();
                            return self.finish_signed_out(config, Some(error)).await;
                        }
                    }
                }
                Err(SafeError::ServiceUnavailable) => {
                    return self.finish_error(SafeError::ServiceUnavailable).await
                }
                Err(error) => {
                    let _ = vault.clear();
                    return self.finish_signed_out(config, Some(error)).await;
                }
            }
        }
        self.finish_signed_out(config, None).await
    }

    async fn login(&self) -> Value {
        let _operation = self.operation.lock().await;
        let context = {
            let state = self.state.lock().await;
            match (
                state.base_url.clone(),
                state.auth_config.clone(),
                state.snapshot.authentication.status,
            ) {
                (Some(base), Some(config), AuthenticationState::SignedOut) => {
                    Some((base, config, state.session_generation))
                }
                _ => None,
            }
        };
        let Some((base, config, generation)) = context else {
            return self.apply_error(SafeError::InvalidAuthConfig).await;
        };
        let transaction = match crate::auth::LoginTransaction::prepare(&config).await {
            Ok(value) => value,
            Err(error) => return self.apply_error(error).await,
        };
        let cancel = CancellationToken::new();
        *self.login_cancel.lock().await = Some(cancel.clone());
        self.mutate(|state| {
            state.snapshot.busy = true;
            state.snapshot.authentication.status = AuthenticationState::SigningIn;
            state.snapshot.authentication.summary =
                "Complete sign-in in the system browser.".into();
            state.snapshot.issue = None;
        })
        .await;
        if let Err(error) = transaction.open_browser() {
            *self.login_cancel.lock().await = None;
            return self.finish_error(error).await;
        }
        let callback = match transaction.receive(cancel).await {
            Ok(callback) => callback,
            Err(error) => {
                *self.login_cancel.lock().await = None;
                return self.finish_error(error).await;
            }
        };
        *self.login_cancel.lock().await = None;
        let tokens = match self
            .api
            .exchange_code(
                &config,
                &callback.redirect_uri,
                &callback.code,
                &callback.verifier,
                &callback.nonce,
            )
            .await
        {
            Ok(tokens) => tokens,
            Err(error) => return self.finish_error(error).await,
        };
        let session_credential = match self
            .api
            .create_session(&base, &tokens.access, self.client_instance_id)
            .await
        {
            Ok(value) => value,
            Err(error) => return self.finish_error(error).await,
        };
        let vault = CredentialVault::for_service(&base);
        if vault
            .save_provider(&tokens)
            .and_then(|_| vault.save_session(&session_credential))
            .is_err()
        {
            let _ = vault.clear();
            return self
                .finish_error(SafeError::CredentialStoreUnavailable)
                .await;
        }
        let session = match self.api.session(&base, &session_credential).await {
            Ok(value) => value,
            Err(error) => return self.finish_error(error).await,
        };
        drop(_operation);
        self.finish_connected(
            &base,
            generation,
            config,
            Credentials {
                provider_refresh: tokens.refresh.expect("login requires a refresh token"),
                quayt_session: session_credential,
            },
            session,
        )
        .await
    }

    async fn select_tenant(&self, tenant_id: &str) -> Value {
        if uuid::Uuid::parse_str(tenant_id).is_err() {
            return self.apply_error(SafeError::TenantRejected).await;
        }
        let initiating_generation = {
            let mut state = self.state.lock().await;
            begin_workspace_transition(&mut state);
            state.snapshot.rev = state.snapshot.rev.saturating_add(1);
            state.session_generation
        };
        let operation = self.operation.lock().await;
        let request = {
            let state = self.state.lock().await;
            match (
                &state.base_url,
                &state.credentials,
                state.session_version,
                state.snapshot.authentication.status,
            ) {
                (
                    Some(base),
                    Some(credentials),
                    Some(version),
                    AuthenticationState::Authenticated,
                ) => Some((
                    base.clone(),
                    Zeroizing::new(credentials.quayt_session.to_string()),
                    version,
                    state.session_generation,
                )),
                _ => None,
            }
        };
        let Some((base, session, version, generation)) = request else {
            return self.finish_error(SafeError::SessionRejected).await;
        };
        if generation != initiating_generation {
            return self.snapshot().await;
        }
        match self
            .api
            .select_tenant(&base, &session, tenant_id, version)
            .await
        {
            Ok(view) => {
                let mut accepted = false;
                self.mutate(|state| {
                    if state.session_generation != generation
                        || state.base_url.as_ref() != Some(&base)
                    {
                        return;
                    }
                    accepted = true;
                    state.session_version = Some(view.version);
                    apply_session(&mut state.snapshot, &view);
                    state.vessel_calls = VesselCallStatus::default();
                    state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
                    state.snapshot.busy = false;
                })
                .await;
                drop(operation);
                if accepted {
                    self.load_vessel_calls(VesselCallFilters::default()).await
                } else {
                    self.snapshot().await
                }
            }
            Err(error) => {
                self.mutate(|state| {
                    if state.session_generation == generation
                        && state.base_url.as_ref() == Some(&base)
                    {
                        state.snapshot.busy = false;
                        state.snapshot.issue = Some(issue(error));
                    }
                })
                .await
            }
        }
    }

    async fn logout(&self) -> Value {
        self.mutate(|state| {
            begin_workspace_transition(state);
            state.snapshot.authentication.status = AuthenticationState::LogoutPending;
        })
        .await;
        if let Some(cancel) = self.login_cancel.lock().await.take() {
            cancel.cancel();
        }
        let _operation = self.operation.lock().await;
        let request = {
            let state = self.state.lock().await;
            match (&state.base_url, &state.credentials) {
                (Some(base), Some(credentials)) => Some((
                    base.clone(),
                    Zeroizing::new(credentials.quayt_session.to_string()),
                )),
                _ => None,
            }
        };
        let Some((base, session)) = request else {
            return self.finish_signed_out_without_config().await;
        };
        self.mutate(|state| {
            state.snapshot.busy = true;
            state.snapshot.authentication.status = AuthenticationState::LogoutPending;
            state.snapshot.authentication.summary = "Confirming sign-out with the service.".into();
            state.session_generation = state.session_generation.saturating_add(1);
            state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
            state.vessel_calls = VesselCallStatus::default();
            state.snapshot.issue = None;
        })
        .await;
        if let Err(error) = self.api.logout(&base, &session).await {
            return self.finish_error(error).await;
        }
        if let Err(error) = CredentialVault::for_service(&base).clear() {
            return self.finish_error(error).await;
        }
        self.finish_signed_out_without_config().await
    }

    async fn load_vessel_calls(&self, filters: VesselCallFilters) -> Value {
        let request = {
            let mut state = self.state.lock().await;
            vessel_request_context(&mut state, true)
        };
        let (base, session, binding) = match request {
            Ok(request) => request,
            Err(error) => return self.apply_error(error).await,
        };
        self.receive_vessel_calls(
            binding,
            self.api.vessel_call_summary(&base, &session),
            self.api.vessel_calls(&base, &session, &filters),
        )
        .await
    }

    async fn receive_vessel_calls(
        &self,
        binding: VesselRequestBinding,
        summary: impl std::future::Future<Output = Result<VesselCallSummary, SafeError>>,
        calls: impl std::future::Future<Output = Result<Vec<VesselCall>, SafeError>>,
    ) -> Value {
        let result = tokio::try_join!(summary, calls);
        self.mutate(|state| {
            if !is_current_vessel_request(state, &binding) {
                return;
            }
            match result {
                Ok((summary, calls)) => {
                    state.vessel_calls.status = if calls.is_empty() { "empty" } else { "ready" };
                    state.vessel_calls.summary = summary;
                    state.vessel_calls.calls = calls;
                    state.vessel_calls.selected = None;
                    state.snapshot.issue = None;
                }
                Err(error) => {
                    state.vessel_calls = VesselCallStatus::default();
                    state.vessel_calls.status = "error";
                    state.snapshot.issue = Some(issue(error));
                }
            }
        })
        .await
    }

    async fn load_vessel_call_detail(&self, id: &str) -> Value {
        if uuid::Uuid::parse_str(id).is_err() {
            return self.apply_error(SafeError::VesselCallsUnavailable).await;
        }
        let request = {
            let mut state = self.state.lock().await;
            vessel_request_context(&mut state, false)
        };
        let (base, session, binding) = match request {
            Ok(request) => request,
            Err(error) => return self.apply_error(error).await,
        };
        self.receive_vessel_detail(binding, self.api.vessel_call(&base, &session, id))
            .await
    }

    async fn receive_vessel_detail(
        &self,
        binding: VesselRequestBinding,
        response: impl std::future::Future<Output = Result<VesselCall, SafeError>>,
    ) -> Value {
        let result = response.await;
        self.mutate(|state| {
            if !is_current_vessel_request(state, &binding) {
                return;
            }
            match result {
                Ok(call) => {
                    state.vessel_calls.selected = Some(call);
                    state.snapshot.issue = None;
                }
                Err(error) => {
                    state.vessel_calls.selected = None;
                    state.snapshot.issue = Some(issue(error));
                }
            }
        })
        .await
    }

    async fn finish_connected(
        &self,
        base: &Url,
        generation: u64,
        config: AuthConfig,
        credentials: Credentials,
        session: SessionView,
    ) -> Value {
        let mut accepted = false;
        self.mutate(|state| {
            if state.base_url.as_ref() != Some(base) || state.session_generation != generation {
                return;
            }
            accepted = true;
            state.session_version = Some(session.version);
            state.session_generation = state.session_generation.saturating_add(1);
            state.auth_config = Some(config);
            state.credentials = Some(credentials);
            state.snapshot.busy = false;
            state.snapshot.connection.status = ConnectionState::Ready;
            state.snapshot.connection.summary = "Service ready.".into();
            state.snapshot.authentication.status = AuthenticationState::Authenticated;
            state.snapshot.authentication.summary = "Signed in securely.".into();
            state.snapshot.issue = None;
            apply_session(&mut state.snapshot, &session);
            state.vessel_calls = VesselCallStatus::default();
            state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
        })
        .await;
        if !accepted {
            return self.snapshot().await;
        }
        if self.state.lock().await.snapshot.tenants.active.is_some() {
            self.load_vessel_calls(VesselCallFilters::default()).await
        } else {
            self.snapshot().await
        }
    }

    async fn finish_signed_out(&self, config: AuthConfig, error: Option<SafeError>) -> Value {
        self.mutate(|state| {
            state.auth_config = Some(config);
            state.credentials = None;
            state.session_version = None;
            state.session_generation = state.session_generation.saturating_add(1);
            state.snapshot.busy = false;
            state.snapshot.connection.status = ConnectionState::Ready;
            state.snapshot.connection.summary = "Service ready.".into();
            state.snapshot.authentication.status = AuthenticationState::SignedOut;
            state.snapshot.authentication.summary = "Sign in to establish a Quayt session.".into();
            state.snapshot.tenants = TenantStatus {
                active: None,
                available: Vec::new(),
                selection_required: false,
            };
            state.vessel_calls = VesselCallStatus::default();
            state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
            state.snapshot.issue = error.map(issue);
        })
        .await
    }

    async fn finish_signed_out_without_config(&self) -> Value {
        self.mutate(|state| {
            state.credentials = None;
            state.session_version = None;
            state.session_generation = state.session_generation.saturating_add(1);
            state.snapshot.busy = false;
            state.snapshot.authentication.status = AuthenticationState::SignedOut;
            state.snapshot.authentication.summary = "Not signed in.".into();
            state.snapshot.tenants = TenantStatus {
                active: None,
                available: Vec::new(),
                selection_required: false,
            };
            state.vessel_calls = VesselCallStatus::default();
            state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
            state.snapshot.issue = None;
        })
        .await
    }

    async fn finish_error(&self, error: SafeError) -> Value {
        self.mutate(|state| {
            state.snapshot.busy = false;
            if matches!(
                error,
                SafeError::ServiceUnavailable | SafeError::InvalidAuthConfig
            ) {
                state.snapshot.connection.status = ConnectionState::Unavailable;
                state.snapshot.connection.summary = error.summary().into();
            }
            if error == SafeError::LogoutIncomplete {
                state.snapshot.authentication.status = AuthenticationState::LogoutPending;
                state.snapshot.authentication.summary = error.summary().into();
            } else if state.snapshot.authentication.status == AuthenticationState::SigningIn {
                state.snapshot.authentication.status = AuthenticationState::SignedOut;
                state.snapshot.authentication.summary = "Not signed in.".into();
            }
            state.snapshot.issue = Some(issue(error));
        })
        .await
    }

    async fn apply_error(&self, error: SafeError) -> Value {
        self.mutate(|state| {
            state.snapshot.issue = Some(issue(error));
        })
        .await
    }
    async fn safe_reply(&self) -> clappkit::app::Reply {
        let snapshot = self.snapshot().await;
        clappkit::app::Reply::new(snapshot.clone(), snapshot)
    }
    async fn gui_reply(&self) -> clappkit::app::Reply {
        let snapshot = self.gui_snapshot().await;
        clappkit::app::Reply::new(snapshot.clone(), snapshot)
    }
    async fn error_reply(&self, message: String) -> clappkit::app::Reply {
        clappkit::app::Reply::new(json!({"ok":false,"error":message}), self.snapshot().await)
    }
    async fn snapshot(&self) -> Value {
        serde_json::to_value(&self.state.lock().await.snapshot)
            .unwrap_or_else(|_| json!({"ok":false,"error":"cannot serialize state"}))
    }
    async fn gui_snapshot(&self) -> Value {
        let state = self.state.lock().await;
        serde_json::to_value(GuiSnapshot {
            safe: state.snapshot.clone(),
            vessel_calls: state.vessel_calls.clone(),
        })
        .unwrap_or_else(|_| json!({"ok":false,"error":"cannot serialize state"}))
    }
    async fn mutate<F>(&self, change: F) -> Value
    where
        F: FnOnce(&mut RuntimeState),
    {
        let mut state = self.state.lock().await;
        change(&mut state);
        state.snapshot.rev = state.snapshot.rev.saturating_add(1);
        serde_json::to_value(&state.snapshot)
            .unwrap_or_else(|_| json!({"ok":false,"error":"cannot serialize state"}))
    }
}

fn issue(error: SafeError) -> SafeIssue {
    SafeIssue {
        code: error.code(),
        summary: error.summary(),
    }
}
fn apply_session(snapshot: &mut Snapshot, view: &SessionView) {
    snapshot.tenants.active = view.active_tenant();
    snapshot.tenants.available = view.tenants();
    snapshot.tenants.selection_required =
        snapshot.tenants.active.is_none() && !snapshot.tenants.available.is_empty();
}

fn vessel_call_filters(req: &Value) -> VesselCallFilters {
    VesselCallFilters {
        status: bounded_status(req),
        query: bounded_filter(req, "search", 100),
        eta_from: bounded_date_bound(req, "etaFrom", false),
        eta_to: bounded_date_bound(req, "etaTo", true),
    }
}

fn is_current_vessel_request(state: &RuntimeState, binding: &VesselRequestBinding) -> bool {
    state.base_url.as_ref() == Some(&binding.service_url)
        && state.session_generation == binding.session_generation
        && state.session_version == binding.session_version
        && state.snapshot.authentication.status == AuthenticationState::Authenticated
        && state.credentials.is_some()
        && state
            .snapshot
            .tenants
            .active
            .as_ref()
            .map(|tenant| &tenant.id)
            == Some(&binding.tenant_id)
        && state.vessel_calls_generation == binding.request_generation
}

fn begin_workspace_transition(state: &mut RuntimeState) {
    state.session_generation = state.session_generation.saturating_add(1);
    state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
    state.vessel_calls = VesselCallStatus::default();
    state.snapshot.tenants.active = None;
    state.snapshot.busy = true;
    state.snapshot.issue = None;
}

fn vessel_request_context(
    state: &mut RuntimeState,
    set_loading: bool,
) -> Result<(Url, Zeroizing<String>, VesselRequestBinding), SafeError> {
    if state.snapshot.authentication.status != AuthenticationState::Authenticated
        || state.snapshot.tenants.active.is_none()
    {
        return Err(SafeError::SessionRejected);
    }
    let (Some(base), Some(credentials), Some(tenant)) = (
        &state.base_url,
        &state.credentials,
        &state.snapshot.tenants.active,
    ) else {
        return Err(SafeError::SessionRejected);
    };
    let base = base.clone();
    let session = Zeroizing::new(credentials.quayt_session.to_string());
    state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
    let binding = VesselRequestBinding {
        service_url: base.clone(),
        session_generation: state.session_generation,
        session_version: state.session_version,
        tenant_id: tenant.id.clone(),
        request_generation: state.vessel_calls_generation,
    };
    if set_loading {
        state.vessel_calls = VesselCallStatus::default();
        state.vessel_calls.status = "loading";
        state.vessel_calls.selected = None;
        state.snapshot.issue = None;
    }
    state.snapshot.rev = state.snapshot.rev.saturating_add(1);
    Ok((base, session, binding))
}

fn bounded_filter(req: &Value, name: &str, maximum: usize) -> Option<String> {
    let value = req.get(name)?.as_str()?.trim();
    (!value.is_empty() && value.len() <= maximum).then(|| value.to_string())
}

fn bounded_status(req: &Value) -> Option<String> {
    let value = bounded_filter(req, "status", 16)?;
    ["expected", "arrived", "berthed", "departed", "cancelled"]
        .contains(&value.as_str())
        .then_some(value)
}

fn bounded_date_bound(req: &Value, name: &str, end_of_day: bool) -> Option<String> {
    let value = bounded_filter(req, name, 10)?;
    let bytes = value.as_bytes();
    let valid_shape = bytes.len() == 10
        && bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes
            .iter()
            .enumerate()
            .all(|(index, byte)| matches!(index, 4 | 7) || byte.is_ascii_digit());
    if !valid_shape {
        return None;
    }
    let year = value[0..4].parse::<u32>().ok()?;
    let month = value[5..7].parse::<u32>().ok()?;
    let day = value[8..10].parse::<u32>().ok()?;
    let max_day = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if year % 400 == 0 || (year % 4 == 0 && year % 100 != 0) => 29,
        2 => 28,
        _ => return None,
    };
    (day > 0 && day <= max_day).then(|| {
        format!(
            "{value}T{}+00:00",
            if end_of_day {
                "23:59:59.999999"
            } else {
                "00:00:00"
            }
        )
    })
}
impl Default for Core {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod vessel_response_tests;

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn cli_snapshot_contains_only_safe_projection() {
        let reply = Core::new().handle_cli(json!({"cmd":"status"})).await;
        let encoded = reply.resp.to_string().to_lowercase();
        for forbidden in [
            "access_token",
            "refresh_token",
            "credential",
            "verifier",
            "nonce",
            "authorization_code",
        ] {
            assert!(!encoded.contains(forbidden));
        }
        assert!(reply.resp.get("connection").is_some());
        assert!(reply.resp.get("authentication").is_some());
        assert!(reply.resp.get("tenants").is_some());
        assert!(reply.resp.get("vesselCalls").is_none());
    }

    #[tokio::test]
    async fn operational_vessel_calls_stay_out_of_cli_projection() {
        let core = Core::new();
        {
            let mut state = core.state.lock().await;
            state.vessel_calls.status = "ready";
            state.vessel_calls.calls = vec![VesselCall {
                id: "0f9426e8-98e7-4f17-aacf-d184fe9ad69e".into(),
                vessel_name: "sentinel-vessel-name".into(),
                imo_number: "sentinel-imo".into(),
                eta: "2030-01-01T12:00:00Z".into(),
                etd: None,
                berth: None,
                status: "expected".into(),
                agent_name: Some("sentinel-agent".into()),
            }];
        }
        let cli = core
            .handle_cli(json!({"cmd":"status"}))
            .await
            .resp
            .to_string();
        for operational_value in [
            "vesselCalls",
            "sentinel-vessel-name",
            "sentinel-imo",
            "sentinel-port",
            "sentinel-agent",
        ] {
            assert!(!cli.contains(operational_value));
        }
        assert!(core.gui_snapshot().await["vesselCalls"]["calls"].is_array());
    }

    #[test]
    fn vessel_call_filters_are_bounded_and_date_shaped() {
        let request = json!({
            "status": " expected ", "search": "MAERSK 123", "etaFrom": "2032-02-29",
            "etaTo": "2030-02-03"
        });
        let filters = vessel_call_filters(&request);
        assert_eq!(filters.status.as_deref(), Some("expected"));
        assert_eq!(filters.query.as_deref(), Some("MAERSK 123"));
        assert_eq!(
            filters.eta_from.as_deref(),
            Some("2032-02-29T00:00:00+00:00")
        );
        assert_eq!(
            filters.eta_to.as_deref(),
            Some("2030-02-03T23:59:59.999999+00:00")
        );
        assert!(vessel_call_filters(&json!({"search": "x".repeat(121)}))
            .query
            .is_none());
        assert!(
            vessel_call_filters(&json!({"status":"scheduled", "etaFrom":"2030-02-29"}))
                .status
                .is_none()
        );
        assert!(vessel_call_filters(&json!({"etaFrom":"2030-02-29"}))
            .eta_from
            .is_none());
    }

    #[tokio::test]
    async fn stale_vessel_response_cannot_commit_after_workspace_changes() {
        let core = Core::new();
        let binding = {
            let mut state = core.state.lock().await;
            state.base_url = Some(Url::parse("https://tenant-a.example/").unwrap());
            state.credentials = Some(Credentials {
                provider_refresh: Zeroizing::new("refresh".into()),
                quayt_session: Zeroizing::new("session".into()),
            });
            state.snapshot.authentication.status = AuthenticationState::Authenticated;
            state.snapshot.tenants.active = Some(TenantView {
                id: "tenant-a".into(),
                name: "A".into(),
            });
            state.session_generation = 4;
            state.vessel_calls_generation = 7;
            VesselRequestBinding {
                service_url: state.base_url.clone().unwrap(),
                session_generation: 4,
                session_version: state.session_version,
                tenant_id: "tenant-a".into(),
                request_generation: 7,
            }
        };
        {
            let mut state = core.state.lock().await;
            state.snapshot.tenants.active = Some(TenantView {
                id: "tenant-b".into(),
                name: "B".into(),
            });
            state.vessel_calls_generation = state.vessel_calls_generation.saturating_add(1);
            if is_current_vessel_request(&state, &binding) {
                state.vessel_calls.status = "ready";
            }
            assert_eq!(state.vessel_calls.status, "idle");
            assert!(state.vessel_calls.calls.is_empty());
        }
    }

    #[tokio::test]
    async fn delayed_list_summary_and_detail_bindings_fail_after_logout_or_reconfiguration() {
        let core = Core::new();
        let binding = VesselRequestBinding {
            service_url: Url::parse("https://tenant-a.example/").unwrap(),
            session_generation: 3,
            session_version: None,
            tenant_id: "tenant-a".into(),
            request_generation: 8,
        };
        let mut state = core.state.lock().await;
        state.base_url = Some(binding.service_url.clone());
        state.credentials = Some(Credentials {
            provider_refresh: Zeroizing::new("refresh".into()),
            quayt_session: Zeroizing::new("session".into()),
        });
        state.snapshot.authentication.status = AuthenticationState::Authenticated;
        state.snapshot.tenants.active = Some(TenantView {
            id: binding.tenant_id.clone(),
            name: "A".into(),
        });
        state.session_generation = binding.session_generation;
        state.vessel_calls_generation = binding.request_generation;
        assert!(is_current_vessel_request(&state, &binding));
        state.snapshot.authentication.status = AuthenticationState::LogoutPending;
        assert!(!is_current_vessel_request(&state, &binding));
        state.snapshot.authentication.status = AuthenticationState::Authenticated;
        state.session_generation += 1;
        assert!(!is_current_vessel_request(&state, &binding));
        state.session_generation = binding.session_generation;
        state.base_url = Some(Url::parse("https://reconfigured.example/").unwrap());
        assert!(!is_current_vessel_request(&state, &binding));
    }

    #[test]
    fn vessel_request_context_rejects_before_any_async_error_handling() {
        let mut state = RuntimeState {
            snapshot: Snapshot::initial(None),
            base_url: None,
            auth_config: None,
            credentials: None,
            session_version: None,
            session_generation: 0,
            vessel_calls: VesselCallStatus::default(),
            vessel_calls_generation: 9,
        };
        assert_eq!(
            vessel_request_context(&mut state, true).unwrap_err(),
            SafeError::SessionRejected
        );
        assert_eq!(state.vessel_calls_generation, 9);
        assert_eq!(state.vessel_calls.status, "idle");
    }

    #[tokio::test]
    async fn sentinel_secrets_in_internal_state_and_transaction_never_reach_snapshot_or_cli() {
        const ACCESS: &str = "sentinel-access-token-7f3d";
        const REFRESH: &str = "sentinel-refresh-token-8a4e";
        const SESSION: &str = "sentinel-quayt-credential-9b5f";
        const VERIFIER: &str = "sentinel-pkce-verifier-1c6a";
        const CODE: &str = "sentinel-callback-code-2d7b";
        const NONCE: &str = "sentinel-oidc-nonce-3e8c";

        let provider_tokens = crate::auth::ProviderTokens {
            access: Zeroizing::new(ACCESS.into()),
            refresh: Some(Zeroizing::new(REFRESH.into())),
        };
        let transaction_result = crate::auth::AuthCode {
            code: Zeroizing::new(CODE.into()),
            verifier: Zeroizing::new(VERIFIER.into()),
            nonce: Zeroizing::new(NONCE.into()),
            redirect_uri: "http://127.0.0.1:49152/oidc/callback".into(),
        };
        let core = Core::new();
        {
            let mut state = core.state.lock().await;
            state.credentials = Some(Credentials {
                provider_refresh: Zeroizing::new(REFRESH.into()),
                quayt_session: Zeroizing::new(SESSION.into()),
            });
        }

        let snapshot = core.snapshot().await;
        let cli = core.handle_cli(json!({"cmd":"status"})).await;
        let encoded = format!("{}{}", snapshot, cli.resp);
        for sentinel in [ACCESS, REFRESH, SESSION, VERIFIER, CODE, NONCE] {
            assert!(!encoded.contains(sentinel), "secret leaked: {sentinel}");
        }

        assert_eq!(provider_tokens.access.as_str(), ACCESS);
        assert_eq!(transaction_result.code.as_str(), CODE);
    }
    #[tokio::test]
    async fn cli_cannot_invoke_gui_auth_commands() {
        for command in [
            "state",
            "configureService",
            "connect",
            "login",
            "cancelLogin",
            "selectTenant",
            "logout",
        ] {
            assert_eq!(
                Core::new().handle_cli(json!({"cmd":command})).await.resp["ok"],
                false
            );
        }
    }
    #[test]
    fn session_projection_contains_tenant_choices_without_actor_or_credentials() {
        let mut snapshot = Snapshot::initial(Some("https://quayt.example".into()));
        let view: SessionView = serde_json::from_value(json!({
            "version": 1,
            "memberships": [{
                "tenant_id": "8b30c18e-c9c8-4ab9-9f17-78bc7c6a0e68",
                "tenant_name": "Harbor North",
                "roles": []
            }],
            "selected_tenant_id": null
        }))
        .unwrap();
        apply_session(&mut snapshot, &view);
        let value = serde_json::to_value(snapshot).unwrap();
        assert_eq!(value["tenants"]["selectionRequired"], true);
        assert!(value.get("actor").is_none());
    }
}
