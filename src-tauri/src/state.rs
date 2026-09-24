use crate::auth::{
    validate_service_url, AuthConfig, CredentialVault, Credentials, SafeError, ServiceApi,
    SessionView, TenantView,
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
            Some("status") => self.reply().await,
            Some(_) => self.error_reply("unsupported command".into()).await,
            None => self.error_reply("missing command".into()).await,
        }
    }

    pub async fn handle_gui(&self, req: Value) -> clappkit::app::Reply {
        match req.get("cmd").and_then(Value::as_str) {
            Some("state") | Some("status") => self.reply().await,
            Some("configureService") => {
                let input = req
                    .get("serviceUrl")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                match self.configure_service(input).await {
                    Ok(value) => self.value_reply(value),
                    Err(error) => self.safe_error_reply(error).await,
                }
            }
            Some("connect") => self.value_reply(self.connect().await),
            Some("login") => self.value_reply(self.login().await),
            Some("cancelLogin") => {
                if let Some(cancel) = self.login_cancel.lock().await.take() {
                    cancel.cancel();
                }
                self.reply().await
            }
            Some("selectTenant") => {
                let tenant_id = req
                    .get("tenantId")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                self.value_reply(self.select_tenant(tenant_id).await)
            }
            Some("logout") => self.value_reply(self.logout().await),
            Some(_) => self.error_reply("unsupported command".into()).await,
            None => self.error_reply("missing command".into()).await,
        }
    }

    async fn configure_service(&self, input: &str) -> Result<Value, SafeError> {
        let base = validate_service_url(input)?;
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
        let Some(base) = self.state.lock().await.base_url.clone() else {
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
                Ok(session) => return self.finish_connected(config, credentials, session).await,
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
                                        return self
                                            .finish_connected(config, refreshed, session)
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
        let (base, config) = {
            let state = self.state.lock().await;
            match (
                state.base_url.clone(),
                state.auth_config.clone(),
                state.snapshot.authentication.status,
            ) {
                (Some(base), Some(config), AuthenticationState::SignedOut) => (base, config),
                _ => return self.apply_error(SafeError::InvalidAuthConfig).await,
            }
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
        self.finish_connected(
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
        let _operation = self.operation.lock().await;
        let (base, session, version) = {
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
                ) => (
                    base.clone(),
                    Zeroizing::new(credentials.quayt_session.to_string()),
                    version,
                ),
                _ => return self.apply_error(SafeError::SessionRejected).await,
            }
        };
        self.mutate(|state| {
            state.snapshot.busy = true;
            state.snapshot.issue = None;
        })
        .await;
        match self
            .api
            .select_tenant(&base, &session, tenant_id, version)
            .await
        {
            Ok(view) => {
                self.mutate(|state| {
                    state.session_version = Some(view.version);
                    apply_session(&mut state.snapshot, &view);
                    state.snapshot.busy = false;
                })
                .await
            }
            Err(error) => self.finish_error(error).await,
        }
    }

    async fn logout(&self) -> Value {
        if let Some(cancel) = self.login_cancel.lock().await.take() {
            cancel.cancel();
        }
        let _operation = self.operation.lock().await;
        let (base, session) = {
            let state = self.state.lock().await;
            match (&state.base_url, &state.credentials) {
                (Some(base), Some(credentials)) => (
                    base.clone(),
                    Zeroizing::new(credentials.quayt_session.to_string()),
                ),
                _ => return self.finish_signed_out_without_config().await,
            }
        };
        self.mutate(|state| {
            state.snapshot.busy = true;
            state.snapshot.authentication.status = AuthenticationState::LogoutPending;
            state.snapshot.authentication.summary = "Confirming sign-out with the service.".into();
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

    async fn finish_connected(
        &self,
        config: AuthConfig,
        credentials: Credentials,
        session: SessionView,
    ) -> Value {
        self.mutate(|state| {
            state.session_version = Some(session.version);
            state.auth_config = Some(config);
            state.credentials = Some(credentials);
            state.snapshot.busy = false;
            state.snapshot.connection.status = ConnectionState::Ready;
            state.snapshot.connection.summary = "Service ready.".into();
            state.snapshot.authentication.status = AuthenticationState::Authenticated;
            state.snapshot.authentication.summary = "Signed in securely.".into();
            state.snapshot.issue = None;
            apply_session(&mut state.snapshot, &session);
        })
        .await
    }

    async fn finish_signed_out(&self, config: AuthConfig, error: Option<SafeError>) -> Value {
        self.mutate(|state| {
            state.auth_config = Some(config);
            state.credentials = None;
            state.session_version = None;
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
            state.snapshot.issue = error.map(issue);
        })
        .await
    }

    async fn finish_signed_out_without_config(&self) -> Value {
        self.mutate(|state| {
            state.credentials = None;
            state.session_version = None;
            state.snapshot.busy = false;
            state.snapshot.authentication.status = AuthenticationState::SignedOut;
            state.snapshot.authentication.summary = "Not signed in.".into();
            state.snapshot.tenants = TenantStatus {
                active: None,
                available: Vec::new(),
                selection_required: false,
            };
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
    async fn safe_error_reply(&self, error: SafeError) -> clappkit::app::Reply {
        self.value_reply(self.apply_error(error).await)
    }
    async fn reply(&self) -> clappkit::app::Reply {
        self.value_reply(self.snapshot().await)
    }
    fn value_reply(&self, snapshot: Value) -> clappkit::app::Reply {
        clappkit::app::Reply::new(snapshot.clone(), snapshot)
    }
    async fn error_reply(&self, message: String) -> clappkit::app::Reply {
        clappkit::app::Reply::new(json!({"ok":false,"error":message}), self.snapshot().await)
    }
    async fn snapshot(&self) -> Value {
        serde_json::to_value(&self.state.lock().await.snapshot)
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
impl Default for Core {
    fn default() -> Self {
        Self::new()
    }
}

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
