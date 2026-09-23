use serde::Serialize;
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::Mutex;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ConnectionStatus {
    status: ConnectionState,
    summary: &'static str,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
enum ConnectionState {
    NotConfigured,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Snapshot {
    ok: bool,
    rev: u64,
    connection: ConnectionStatus,
}

#[derive(Clone)]
pub struct Core {
    state: Arc<Mutex<Snapshot>>,
}

impl Core {
    pub fn new() -> Self {
        Self {
            state: Arc::new(Mutex::new(Snapshot {
                ok: true,
                rev: 0,
                connection: ConnectionStatus {
                    status: ConnectionState::NotConfigured,
                    summary: "Service connection is not configured.",
                },
            })),
        }
    }

    pub async fn handle(&self, req: Value) -> clappkit::app::Reply {
        let snapshot = serde_json::to_value(&*self.state.lock().await)
            .unwrap_or_else(|_| json!({"ok":false,"error":"cannot serialize state"}));
        let response = match req.get("cmd").and_then(Value::as_str) {
            Some("status") | Some("state") => snapshot.clone(),
            Some(command) => json!({"ok":false,"error":format!("unsupported command '{command}'")}),
            None => json!({"ok":false,"error":"missing command"}),
        };
        clappkit::app::Reply::new(response, snapshot)
    }
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
    async fn snapshot_is_connection_status_only() {
        let reply = Core::new().handle(json!({"cmd":"status"})).await;
        assert_eq!(reply.resp["ok"], true);
        assert_eq!(reply.resp["rev"], 0);
        assert_eq!(reply.resp["connection"]["status"], "notConfigured");
        let keys: Vec<_> = reply
            .resp
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(keys, ["connection", "ok", "rev"]);
    }
    #[tokio::test]
    async fn operational_commands_are_rejected() {
        assert_eq!(
            Core::new().handle(json!({"cmd":"run"})).await.resp["ok"],
            false
        );
    }
}
