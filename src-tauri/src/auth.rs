use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::future::Future;
use std::net::Ipv4Addr;
use std::time::Duration;
use subtle::ConstantTimeEq;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio_util::sync::CancellationToken;
use url::{Host, Url};
use zeroize::{Zeroize, Zeroizing};

const CALLBACK_PATH: &str = "/oidc/callback";
const CALLBACK_TIMEOUT: Duration = Duration::from_secs(120);
const CALLBACK_READ_TIMEOUT: Duration = Duration::from_secs(3);
const MAX_CALLBACK_REQUESTS: usize = 8;
const MAX_CALLBACK_BYTES: usize = 8192;
const CREDENTIAL_SERVICE: &str = "com.arfium.quayt";

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AuthConfig {
    pub issuer: String,
    #[serde(alias = "authorization_endpoint")]
    pub authorization_endpoint: String,
    #[serde(alias = "token_endpoint")]
    pub token_endpoint: String,
    #[serde(alias = "client_id")]
    pub client_id: String,
    #[serde(default)]
    pub scopes: Vec<String>,
    #[serde(default, alias = "pkce_methods")]
    pub pkce_methods: Vec<String>,
    #[serde(alias = "loopback_host")]
    pub loopback_host: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TenantView {
    #[serde(alias = "tenant_id")]
    pub id: String,
    #[serde(alias = "display_name")]
    pub name: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionView {
    pub version: u64,
    #[serde(default)]
    memberships: Vec<MembershipView>,
    #[serde(default, alias = "selected_tenant_id")]
    selected_tenant_id: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct MembershipView {
    #[serde(alias = "tenantId")]
    tenant_id: String,
    #[serde(alias = "tenantName")]
    tenant_name: String,
}

impl SessionView {
    pub fn tenants(&self) -> Vec<TenantView> {
        self.memberships
            .iter()
            .map(|membership| TenantView {
                id: membership.tenant_id.clone(),
                name: membership.tenant_name.clone(),
            })
            .collect()
    }

    pub fn active_tenant(&self) -> Option<TenantView> {
        let selected = self.selected_tenant_id.as_deref()?;
        self.memberships
            .iter()
            .find(|membership| membership.tenant_id == selected)
            .map(|membership| TenantView {
                id: membership.tenant_id.clone(),
                name: membership.tenant_name.clone(),
            })
    }
}

pub struct Credentials {
    pub provider_refresh: Zeroizing<String>,
    pub quayt_session: Zeroizing<String>,
}

pub struct ProviderTokens {
    pub access: Zeroizing<String>,
    pub refresh: Option<Zeroizing<String>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SafeError {
    InvalidServiceUrl,
    ServiceUnavailable,
    InvalidAuthConfig,
    BrowserUnavailable,
    LoginCancelled,
    LoginTimedOut,
    LoginRejected,
    CredentialStoreUnavailable,
    SessionRejected,
    TenantRejected,
    LogoutIncomplete,
}

impl SafeError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidServiceUrl => "invalidServiceUrl",
            Self::ServiceUnavailable => "serviceUnavailable",
            Self::InvalidAuthConfig => "invalidAuthConfig",
            Self::BrowserUnavailable => "browserUnavailable",
            Self::LoginCancelled => "loginCancelled",
            Self::LoginTimedOut => "loginTimedOut",
            Self::LoginRejected => "loginRejected",
            Self::CredentialStoreUnavailable => "credentialStoreUnavailable",
            Self::SessionRejected => "sessionRejected",
            Self::TenantRejected => "tenantRejected",
            Self::LogoutIncomplete => "logoutIncomplete",
        }
    }

    pub fn summary(self) -> &'static str {
        match self {
            Self::InvalidServiceUrl => {
                "Enter a valid HTTPS service URL or an explicit loopback development URL."
            }
            Self::ServiceUnavailable => "The Quayt service is not ready.",
            Self::InvalidAuthConfig => "The service returned invalid sign-in configuration.",
            Self::BrowserUnavailable => "Quayt could not open the system browser.",
            Self::LoginCancelled => "Sign-in was cancelled.",
            Self::LoginTimedOut => "Sign-in timed out. Start again when ready.",
            Self::LoginRejected => "Sign-in could not be completed.",
            Self::CredentialStoreUnavailable => "Secure credential storage is unavailable.",
            Self::SessionRejected => "The Quayt session is no longer valid.",
            Self::TenantRejected => "The service did not accept that workspace.",
            Self::LogoutIncomplete => {
                "Sign-out could not be confirmed. Quayt will not use the stored session."
            }
        }
    }
}

#[derive(Clone)]
pub struct ServiceApi {
    http: Client,
}

impl ServiceApi {
    pub fn new() -> Self {
        Self {
            http: Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .connect_timeout(Duration::from_secs(5))
                .timeout(Duration::from_secs(12))
                .build()
                .expect("fixed HTTPS client configuration"),
        }
    }

    pub async fn discover(&self, base: &Url) -> Result<AuthConfig, SafeError> {
        let ready = endpoint(base, "/health/ready")?;
        let response = self
            .http
            .get(ready)
            .send()
            .await
            .map_err(|_| SafeError::ServiceUnavailable)?;
        if !response.status().is_success() {
            return Err(SafeError::ServiceUnavailable);
        }
        let response = self
            .http
            .get(endpoint(base, "/api/v1/auth/config")?)
            .send()
            .await
            .map_err(|_| SafeError::ServiceUnavailable)?;
        if !response.status().is_success() {
            return Err(SafeError::InvalidAuthConfig);
        }
        let config: AuthConfig = response
            .json()
            .await
            .map_err(|_| SafeError::InvalidAuthConfig)?;
        validate_auth_config(&config)?;
        Ok(config)
    }

    pub async fn exchange_code(
        &self,
        config: &AuthConfig,
        redirect_uri: &str,
        code: &str,
        verifier: &str,
        nonce: &str,
    ) -> Result<ProviderTokens, SafeError> {
        let response = self
            .http
            .post(&config.token_endpoint)
            .form(&[
                ("grant_type", "authorization_code"),
                ("client_id", config.client_id.as_str()),
                ("code", code),
                ("redirect_uri", redirect_uri),
                ("code_verifier", verifier),
            ])
            .send()
            .await
            .map_err(|_| SafeError::LoginRejected)?;
        if !response.status().is_success() {
            return Err(SafeError::LoginRejected);
        }
        let token: TokenResponse = response
            .json()
            .await
            .map_err(|_| SafeError::LoginRejected)?;
        if !token.token_type.eq_ignore_ascii_case("bearer") || token.access_token.is_empty() {
            return Err(SafeError::LoginRejected);
        }
        validate_id_token_binding(&token.id_token, nonce, &config.issuer, &config.client_id)?;
        let refresh = token.refresh_token.ok_or(SafeError::LoginRejected)?;
        Ok(ProviderTokens {
            access: Zeroizing::new(token.access_token),
            refresh: Some(Zeroizing::new(refresh)),
        })
    }

    pub async fn refresh_provider(
        &self,
        config: &AuthConfig,
        refresh: &str,
    ) -> Result<ProviderTokens, SafeError> {
        let response = self
            .http
            .post(&config.token_endpoint)
            .form(&[
                ("grant_type", "refresh_token"),
                ("client_id", config.client_id.as_str()),
                ("refresh_token", refresh),
            ])
            .send()
            .await
            .map_err(|_| SafeError::ServiceUnavailable)?;
        if !response.status().is_success() {
            return Err(SafeError::SessionRejected);
        }
        let token: RefreshResponse = response
            .json()
            .await
            .map_err(|_| SafeError::SessionRejected)?;
        Ok(ProviderTokens {
            access: Zeroizing::new(token.access_token),
            refresh: Some(Zeroizing::new(
                token.refresh_token.unwrap_or_else(|| refresh.to_string()),
            )),
        })
    }

    pub async fn create_session(
        &self,
        base: &Url,
        access: &str,
        client_instance_id: uuid::Uuid,
    ) -> Result<Zeroizing<String>, SafeError> {
        let response = self
            .http
            .post(endpoint(base, "/api/v1/session")?)
            .bearer_auth(access)
            .json(&serde_json::json!({ "client_instance_id": client_instance_id }))
            .send()
            .await
            .map_err(|_| SafeError::ServiceUnavailable)?;
        credential_response(response, SafeError::SessionRejected).await
    }

    pub async fn rotate_session(
        &self,
        base: &Url,
        session: &str,
        provider_access: &str,
    ) -> Result<Zeroizing<String>, SafeError> {
        let response = self
            .http
            .post(endpoint(base, "/api/v1/session/refresh")?)
            .bearer_auth(provider_access)
            .header("X-Quayt-Session-Credential", session)
            .send()
            .await
            .map_err(|_| SafeError::ServiceUnavailable)?;
        credential_response(response, SafeError::SessionRejected).await
    }

    pub async fn session(&self, base: &Url, session: &str) -> Result<SessionView, SafeError> {
        let response = self
            .http
            .get(endpoint(base, "/api/v1/session")?)
            .header(reqwest::header::AUTHORIZATION, format!("Session {session}"))
            .send()
            .await
            .map_err(|_| SafeError::ServiceUnavailable)?;
        if response.status() == StatusCode::UNAUTHORIZED {
            return Err(SafeError::SessionRejected);
        }
        if !response.status().is_success() {
            return Err(SafeError::ServiceUnavailable);
        }
        response
            .json()
            .await
            .map_err(|_| SafeError::SessionRejected)
    }

    pub async fn select_tenant(
        &self,
        base: &Url,
        session: &str,
        tenant_id: &str,
        expected_version: u64,
    ) -> Result<SessionView, SafeError> {
        let response = self
            .http
            .post(endpoint(base, "/api/v1/session/tenant")?)
            .header(reqwest::header::AUTHORIZATION, format!("Session {session}"))
            .json(&serde_json::json!({ "tenant_id": tenant_id, "expected_session_version": expected_version }))
            .send()
            .await
            .map_err(|_| SafeError::ServiceUnavailable)?;
        if response.status().is_success() {
            response.json().await.map_err(|_| SafeError::TenantRejected)
        } else {
            Err(SafeError::TenantRejected)
        }
    }

    pub async fn logout(&self, base: &Url, session: &str) -> Result<(), SafeError> {
        let response = self
            .http
            .post(endpoint(base, "/api/v1/session/logout")?)
            .header(reqwest::header::AUTHORIZATION, format!("Session {session}"))
            .send()
            .await
            .map_err(|_| SafeError::LogoutIncomplete)?;
        if response.status().is_success() {
            Ok(())
        } else {
            Err(SafeError::LogoutIncomplete)
        }
    }
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    refresh_token: Option<String>,
    id_token: String,
    token_type: String,
}

#[derive(Deserialize)]
struct RefreshResponse {
    access_token: String,
    refresh_token: Option<String>,
}

#[derive(Deserialize)]
struct CredentialResponse {
    session_credential: String,
}

async fn credential_response(
    response: reqwest::Response,
    error: SafeError,
) -> Result<Zeroizing<String>, SafeError> {
    if !response.status().is_success() {
        return Err(error);
    }
    let body: CredentialResponse = response.json().await.map_err(|_| error)?;
    if body.session_credential.is_empty() {
        return Err(error);
    }
    Ok(Zeroizing::new(body.session_credential))
}

#[derive(Clone)]
pub struct CredentialVault {
    scope: String,
}

impl CredentialVault {
    pub fn for_service(base: &Url) -> Self {
        let digest = Sha256::digest(base.as_str().as_bytes());
        Self {
            scope: URL_SAFE_NO_PAD.encode(&digest[..18]),
        }
    }

    pub fn load(&self) -> Result<Option<Credentials>, SafeError> {
        let Some(provider_access) = self.read("provider-access")? else {
            return Ok(None);
        };
        let _provider_access = Zeroizing::new(provider_access);
        let Some(provider_refresh) = self.read("provider-refresh")? else {
            return Ok(None);
        };
        let Some(quayt_session) = self.read("quayt-session")? else {
            return Ok(None);
        };
        Ok(Some(Credentials {
            provider_refresh: Zeroizing::new(provider_refresh),
            quayt_session: Zeroizing::new(quayt_session),
        }))
    }

    pub fn save_provider(&self, tokens: &ProviderTokens) -> Result<(), SafeError> {
        self.write("provider-access", &tokens.access)?;
        if let Some(refresh) = &tokens.refresh {
            self.write("provider-refresh", refresh)?;
        }
        Ok(())
    }

    pub fn save_session(&self, session: &str) -> Result<(), SafeError> {
        self.write("quayt-session", session)
    }

    pub fn clear(&self) -> Result<(), SafeError> {
        for name in ["provider-access", "provider-refresh", "quayt-session"] {
            let entry = self.entry(name)?;
            match entry.delete_credential() {
                Ok(()) | Err(keyring::Error::NoEntry) => {}
                Err(_) => return Err(SafeError::CredentialStoreUnavailable),
            }
        }
        Ok(())
    }

    fn entry(&self, name: &str) -> Result<keyring::Entry, SafeError> {
        keyring::Entry::new(CREDENTIAL_SERVICE, &format!("{}:{name}", self.scope))
            .map_err(|_| SafeError::CredentialStoreUnavailable)
    }

    fn read(&self, name: &str) -> Result<Option<String>, SafeError> {
        match self.entry(name)?.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(_) => Err(SafeError::CredentialStoreUnavailable),
        }
    }

    fn write(&self, name: &str, secret: &str) -> Result<(), SafeError> {
        self.entry(name)?
            .set_password(secret)
            .map_err(|_| SafeError::CredentialStoreUnavailable)
    }
}

pub struct LoginTransaction {
    pub authorization_url: Url,
    pub redirect_uri: String,
    verifier: Zeroizing<String>,
    state: CallbackState,
    nonce: Zeroizing<String>,
    listener: TcpListener,
}

impl LoginTransaction {
    pub async fn prepare(config: &AuthConfig) -> Result<Self, SafeError> {
        validate_auth_config(config)?;
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
            .await
            .map_err(|_| SafeError::LoginRejected)?;
        let port = listener
            .local_addr()
            .map_err(|_| SafeError::LoginRejected)?
            .port();
        let redirect_uri = format!("http://127.0.0.1:{port}{CALLBACK_PATH}");
        let verifier = random_urlsafe(64)?;
        let state = random_urlsafe(32)?;
        let nonce = random_urlsafe(32)?;
        let authorization_url =
            build_authorization_url(config, &redirect_uri, &verifier, &state, &nonce)?;
        Ok(Self {
            authorization_url,
            redirect_uri,
            verifier,
            state: CallbackState::new(state),
            nonce,
            listener,
        })
    }

    pub fn open_browser(&self) -> Result<(), SafeError> {
        open::that_detached(self.authorization_url.as_str())
            .map_err(|_| SafeError::BrowserUnavailable)
    }

    pub async fn receive(self, cancel: CancellationToken) -> Result<AuthCode, SafeError> {
        bounded_callback(cancel, CALLBACK_TIMEOUT, self.receive_requests()).await
    }

    async fn receive_requests(mut self) -> Result<AuthCode, SafeError> {
        let expected_host = format!(
            "127.0.0.1:{}",
            self.listener
                .local_addr()
                .map_err(|_| SafeError::LoginRejected)?
                .port()
        );
        for _ in 0..MAX_CALLBACK_REQUESTS {
            let accepted = self
                .listener
                .accept()
                .await
                .map_err(|_| SafeError::LoginRejected)?;
            let (mut stream, peer) = accepted;
            if !peer.ip().is_loopback() {
                continue;
            }
            let request = match tokio::time::timeout(
                CALLBACK_READ_TIMEOUT,
                read_callback_request(&mut stream),
            )
            .await
            {
                Ok(Ok(value)) => value,
                _ => continue,
            };
            let target = match callback_target(&request, &expected_host) {
                Ok(target) => target,
                Err(_) => continue,
            };
            if target.split('?').next() != Some(CALLBACK_PATH) {
                continue;
            }
            let result = self.state.consume(&target);
            let successful = result.is_ok();
            let response = if successful {
                "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n<!doctype html><title>Quayt sign-in complete</title><p>Sign-in complete. Return to Quayt.</p>"
            } else {
                "HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n<!doctype html><title>Quayt sign-in failed</title><p>Sign-in could not be completed. Return to Quayt.</p>"
            };
            let _ = stream.write_all(response.as_bytes()).await;
            let code = result?;
            return Ok(AuthCode {
                code,
                verifier: self.verifier,
                nonce: self.nonce,
                redirect_uri: self.redirect_uri,
            });
        }
        Err(SafeError::LoginRejected)
    }
}

async fn bounded_callback<T, F>(
    cancel: CancellationToken,
    timeout: Duration,
    callback: F,
) -> Result<T, SafeError>
where
    F: Future<Output = Result<T, SafeError>>,
{
    tokio::select! {
        biased;
        _ = cancel.cancelled() => Err(SafeError::LoginCancelled),
        _ = tokio::time::sleep(timeout) => Err(SafeError::LoginTimedOut),
        result = callback => result,
    }
}

struct CallbackState {
    expected: Option<Zeroizing<String>>,
}

impl CallbackState {
    fn new(expected: Zeroizing<String>) -> Self {
        Self {
            expected: Some(expected),
        }
    }

    fn consume(&mut self, target: &str) -> Result<Zeroizing<String>, SafeError> {
        let expected = self.expected.take().ok_or(SafeError::LoginRejected)?;
        parse_callback(target, &expected)
    }
}

pub struct AuthCode {
    pub code: Zeroizing<String>,
    pub verifier: Zeroizing<String>,
    pub nonce: Zeroizing<String>,
    pub redirect_uri: String,
}

pub fn validate_service_url(input: &str) -> Result<Url, SafeError> {
    let mut url = Url::parse(input.trim()).map_err(|_| SafeError::InvalidServiceUrl)?;
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(SafeError::InvalidServiceUrl);
    }
    let secure = url.scheme() == "https";
    let loopback = url.scheme() == "http" && is_loopback_host(&url);
    if !secure && !loopback {
        return Err(SafeError::InvalidServiceUrl);
    }
    if url.host_str().is_none() || (url.path() != "/" && !url.path().is_empty()) {
        return Err(SafeError::InvalidServiceUrl);
    }
    url.set_path("/");
    Ok(url)
}

fn validate_auth_config(config: &AuthConfig) -> Result<(), SafeError> {
    if config.client_id.trim().is_empty()
        || !config.pkce_methods.iter().any(|method| method == "S256")
        || config.loopback_host != "127.0.0.1"
    {
        return Err(SafeError::InvalidAuthConfig);
    }
    for raw in [
        &config.issuer,
        &config.authorization_endpoint,
        &config.token_endpoint,
    ] {
        let url = Url::parse(raw).map_err(|_| SafeError::InvalidAuthConfig)?;
        if url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(SafeError::InvalidAuthConfig);
        }
        let secure = url.scheme() == "https";
        let loopback = url.scheme() == "http" && is_loopback_host(&url);
        if !secure && !loopback {
            return Err(SafeError::InvalidAuthConfig);
        }
    }
    Ok(())
}

fn endpoint(base: &Url, path: &str) -> Result<Url, SafeError> {
    base.join(path.trim_start_matches('/'))
        .map_err(|_| SafeError::InvalidServiceUrl)
}

fn scopes(config: &AuthConfig) -> String {
    let mut scopes = config.scopes.clone();
    if !scopes.iter().any(|scope| scope == "openid") {
        scopes.insert(0, "openid".into());
    }
    scopes.join(" ")
}

fn is_loopback_host(url: &Url) -> bool {
    match url.host() {
        Some(Host::Ipv4(address)) => address.is_loopback(),
        Some(Host::Ipv6(address)) => address.is_loopback(),
        Some(Host::Domain(domain)) => domain.eq_ignore_ascii_case("localhost"),
        None => false,
    }
}

fn build_authorization_url(
    config: &AuthConfig,
    redirect_uri: &str,
    verifier: &str,
    state: &str,
    nonce: &str,
) -> Result<Url, SafeError> {
    let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
    let mut url =
        Url::parse(&config.authorization_endpoint).map_err(|_| SafeError::InvalidAuthConfig)?;
    {
        let mut query = url.query_pairs_mut();
        query.append_pair("response_type", "code");
        query.append_pair("client_id", &config.client_id);
        query.append_pair("redirect_uri", redirect_uri);
        query.append_pair("scope", &scopes(config));
        query.append_pair("state", state);
        query.append_pair("nonce", nonce);
        query.append_pair("code_challenge", &challenge);
        query.append_pair("code_challenge_method", "S256");
    }
    Ok(url)
}

fn random_urlsafe(bytes: usize) -> Result<Zeroizing<String>, SafeError> {
    let mut random = vec![0_u8; bytes];
    getrandom::fill(&mut random).map_err(|_| SafeError::LoginRejected)?;
    let encoded = Zeroizing::new(URL_SAFE_NO_PAD.encode(&random));
    random.zeroize();
    Ok(encoded)
}

async fn read_callback_request(stream: &mut tokio::net::TcpStream) -> Result<String, SafeError> {
    let mut bytes = Vec::with_capacity(1024);
    loop {
        let remaining = MAX_CALLBACK_BYTES.saturating_sub(bytes.len());
        if remaining == 0 {
            return Err(SafeError::LoginRejected);
        }
        let mut chunk = [0_u8; 1024];
        let read_limit = remaining.min(chunk.len());
        let count = stream
            .read(&mut chunk[..read_limit])
            .await
            .map_err(|_| SafeError::LoginRejected)?;
        if count == 0 {
            return Err(SafeError::LoginRejected);
        }
        bytes.extend_from_slice(&chunk[..count]);
        if bytes.windows(4).any(|window| window == b"\r\n\r\n") {
            return String::from_utf8(bytes).map_err(|_| SafeError::LoginRejected);
        }
    }
}

fn callback_target(request: &str, expected_host: &str) -> Result<String, SafeError> {
    let mut lines = request.split("\r\n");
    let first = lines.next().ok_or(SafeError::LoginRejected)?;
    let mut parts = first.split_whitespace();
    if parts.next() != Some("GET") {
        return Err(SafeError::LoginRejected);
    }
    let target = parts.next().ok_or(SafeError::LoginRejected)?;
    if parts.next() != Some("HTTP/1.1") || parts.next().is_some() {
        return Err(SafeError::LoginRejected);
    }
    let mut host = None;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        if name.eq_ignore_ascii_case("host") {
            if host.is_some() {
                return Err(SafeError::LoginRejected);
            }
            host = Some(value.trim());
        }
    }
    if host != Some(expected_host) {
        return Err(SafeError::LoginRejected);
    }
    Ok(target.to_string())
}

fn parse_callback(target: &str, expected_state: &str) -> Result<Zeroizing<String>, SafeError> {
    let url =
        Url::parse(&format!("http://127.0.0.1{target}")).map_err(|_| SafeError::LoginRejected)?;
    if url.path() != CALLBACK_PATH {
        return Err(SafeError::LoginRejected);
    }
    let mut code = None;
    let mut state = None;
    let mut provider_error = false;
    for (key, value) in url.query_pairs() {
        match key.as_ref() {
            "code" if code.is_none() => code = Some(value.into_owned()),
            "state" if state.is_none() => state = Some(value.into_owned()),
            "error" => provider_error = true,
            "code" | "state" => return Err(SafeError::LoginRejected),
            _ => {}
        }
    }
    if provider_error {
        return Err(SafeError::LoginRejected);
    }
    let state = state.ok_or(SafeError::LoginRejected)?;
    if state
        .as_bytes()
        .ct_eq(expected_state.as_bytes())
        .unwrap_u8()
        != 1
    {
        return Err(SafeError::LoginRejected);
    }
    code.filter(|value| !value.is_empty())
        .map(Zeroizing::new)
        .ok_or(SafeError::LoginRejected)
}

fn validate_id_token_binding(
    id_token: &str,
    expected_nonce: &str,
    expected_issuer: &str,
    expected_audience: &str,
) -> Result<(), SafeError> {
    // The ID token is never used as identity or authority; the service independently
    // verifies the provider access token. This claim check only binds the native login
    // transaction to the response before any credential is accepted locally.
    let segments: Vec<_> = id_token.split('.').collect();
    if segments.len() != 3 || segments.iter().any(|segment| segment.is_empty()) {
        return Err(SafeError::LoginRejected);
    }
    let payload = segments[1];
    let decoded = URL_SAFE_NO_PAD
        .decode(payload)
        .map_err(|_| SafeError::LoginRejected)?;
    let claims: IdClaims =
        serde_json::from_slice(&decoded).map_err(|_| SafeError::LoginRejected)?;
    let nonce_matches = claims
        .nonce
        .as_bytes()
        .ct_eq(expected_nonce.as_bytes())
        .unwrap_u8()
        == 1;
    let audience_matches = match claims.aud {
        Audience::One(audience) => audience == expected_audience,
        Audience::Many(audiences) => audiences
            .iter()
            .any(|audience| audience == expected_audience),
    };
    if nonce_matches && claims.iss == expected_issuer && audience_matches {
        Ok(())
    } else {
        Err(SafeError::LoginRejected)
    }
}

#[derive(Deserialize)]
struct IdClaims {
    nonce: String,
    iss: String,
    aud: Audience,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Audience {
    One(String),
    Many(Vec<String>),
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> AuthConfig {
        AuthConfig {
            issuer: "https://identity.example".into(),
            authorization_endpoint: "https://identity.example/authorize".into(),
            token_endpoint: "https://identity.example/token".into(),
            client_id: "quayt-native".into(),
            scopes: vec!["openid".into(), "profile".into()],
            pkce_methods: vec!["S256".into()],
            loopback_host: "127.0.0.1".into(),
        }
    }

    #[test]
    fn service_urls_fail_closed() {
        assert!(validate_service_url("https://quayt.example").is_ok());
        assert!(validate_service_url("http://127.0.0.1:8080").is_ok());
        assert!(validate_service_url("http://[::1]:8080").is_ok());
        assert!(validate_service_url("http://localhost:8080").is_ok());
        for bad in [
            "http://quayt.example",
            "https://u:p@quayt.example",
            "https://quayt.example/path",
        ] {
            assert_eq!(
                validate_service_url(bad).unwrap_err(),
                SafeError::InvalidServiceUrl
            );
        }
    }

    #[test]
    fn authorization_url_uses_loopback_pkce_state_and_nonce() {
        let url = build_authorization_url(
            &config(),
            "http://127.0.0.1:49152/oidc/callback",
            "verifier-with-enough-entropy-for-a-native-client",
            "unpredictable-state-value-1234567890",
            "unpredictable-nonce-value-1234567890",
        )
        .unwrap();
        let query: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
        assert_eq!(
            query.get("code_challenge_method").map(String::as_str),
            Some("S256")
        );
        assert!(query
            .get("redirect_uri")
            .unwrap()
            .starts_with("http://127.0.0.1:"));
        assert!(query.get("redirect_uri").unwrap().ends_with(CALLBACK_PATH));
        assert!(query.get("state").is_some_and(|value| value.len() >= 32));
        assert!(query.get("nonce").is_some_and(|value| value.len() >= 32));
    }

    #[test]
    fn callback_requires_exact_single_state_and_code() {
        assert_eq!(
            parse_callback("/oidc/callback?code=good&state=expected", "expected")
                .unwrap()
                .as_str(),
            "good"
        );
        for bad in [
            "/other?code=good&state=expected",
            "/oidc/callback?code=good&state=wrong",
            "/oidc/callback?code=a&code=b&state=expected",
            "/oidc/callback?error=denied&state=expected",
        ] {
            assert_eq!(
                parse_callback(bad, "expected").unwrap_err(),
                SafeError::LoginRejected
            );
        }
    }

    #[tokio::test(start_paused = true)]
    async fn callback_wait_has_an_executable_bound_without_changing_production_timeout() {
        assert_eq!(CALLBACK_TIMEOUT, Duration::from_secs(120));
        let result = bounded_callback(
            CancellationToken::new(),
            Duration::from_secs(7),
            std::future::pending::<Result<(), SafeError>>(),
        )
        .await;
        assert_eq!(result.unwrap_err(), SafeError::LoginTimedOut);
    }

    #[tokio::test]
    async fn callback_wait_honors_cancellation() {
        let cancel = CancellationToken::new();
        cancel.cancel();
        let result = bounded_callback(
            cancel,
            CALLBACK_TIMEOUT,
            std::future::pending::<Result<(), SafeError>>(),
        )
        .await;
        assert_eq!(result.unwrap_err(), SafeError::LoginCancelled);
    }

    #[test]
    fn callback_state_is_consumed_once_and_replay_is_rejected() {
        let mut state = CallbackState::new(Zeroizing::new("sentinel-state".into()));
        let target = "/oidc/callback?code=sentinel-code&state=sentinel-state";
        assert_eq!(state.consume(target).unwrap().as_str(), "sentinel-code");
        assert_eq!(state.consume(target).unwrap_err(), SafeError::LoginRejected);

        let mut failed = CallbackState::new(Zeroizing::new("sentinel-state".into()));
        assert_eq!(
            failed
                .consume("/oidc/callback?code=x&state=wrong")
                .unwrap_err(),
            SafeError::LoginRejected
        );
        assert_eq!(
            failed.consume(target).unwrap_err(),
            SafeError::LoginRejected
        );
    }

    #[test]
    fn oidc_response_binding_requires_jwt_shape_nonce_issuer_and_audience() {
        fn token(nonce: &str, issuer: &str, audience: serde_json::Value) -> String {
            let payload = URL_SAFE_NO_PAD.encode(
                serde_json::to_vec(&serde_json::json!({
                    "nonce": nonce,
                    "iss": issuer,
                    "aud": audience
                }))
                .unwrap(),
            );
            format!("header.{payload}.signature")
        }

        let valid = token(
            "sentinel-nonce",
            "https://identity.example",
            serde_json::json!(["another-client", "quayt-native"]),
        );
        assert!(validate_id_token_binding(
            &valid,
            "sentinel-nonce",
            "https://identity.example",
            "quayt-native"
        )
        .is_ok());
        assert!(validate_id_token_binding(
            &valid,
            "wrong-nonce",
            "https://identity.example",
            "quayt-native"
        )
        .is_err());
        assert!(validate_id_token_binding(
            &valid,
            "sentinel-nonce",
            "https://other.example",
            "quayt-native"
        )
        .is_err());
        assert!(validate_id_token_binding(
            "header.payload",
            "sentinel-nonce",
            "https://identity.example",
            "quayt-native"
        )
        .is_err());
    }

    #[test]
    fn callback_request_requires_exact_loopback_host_and_http_shape() {
        let request = "GET /oidc/callback?code=x&state=y HTTP/1.1\r\nHost: 127.0.0.1:49152\r\n\r\n";
        assert!(callback_target(request, "127.0.0.1:49152").is_ok());
        assert!(callback_target(request, "127.0.0.1:49153").is_err());
        assert!(callback_target(
            "POST /oidc/callback HTTP/1.1\r\nHost: 127.0.0.1:49152\r\n\r\n",
            "127.0.0.1:49152"
        )
        .is_err());
    }

    #[test]
    fn backend_session_contract_maps_to_safe_tenant_projection() {
        let view: SessionView = serde_json::from_value(serde_json::json!({
            "session_id": "be18804c-9f48-4ed3-9ccd-f54947003484",
            "version": 3,
            "actor": {"id":"847e45c3-b593-464f-a767-436bdd773a78","display_name":"Operator"},
            "memberships": [{
                "tenant_id":"8b30c18e-c9c8-4ab9-9f17-78bc7c6a0e68",
                "tenant_name":"Harbor North",
                "roles":["operator"]
            }],
            "selected_tenant_id":"8b30c18e-c9c8-4ab9-9f17-78bc7c6a0e68",
            "expires_at":"2030-01-01T00:00:00Z"
        }))
        .unwrap();
        assert_eq!(view.version, 3);
        assert_eq!(view.active_tenant().unwrap().name, "Harbor North");
        assert_eq!(view.tenants().len(), 1);
    }
}
