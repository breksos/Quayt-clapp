use crate::{state::Core, CLI};
use serde_json::Value;
use tauri::{AppHandle, Manager, State};

const ICON: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), "/../assets/icon.png"));
fn window_policy() -> clappkit::WindowPolicy {
    clappkit::WindowPolicy::backgroundable(ICON)
}

#[tauri::command]
async fn run_cmd(req: Value, app: AppHandle, core: State<'_, Core>) -> Result<Value, String> {
    if let Some(response) = clappkit::app::window_cmd(&app, &req, window_policy()) {
        return Ok(response);
    }
    let reply = core.handle_gui(req).await;
    clappkit::app::push_state(&app, reply.snapshot);
    Ok(reply.resp)
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            clappkit::app::apply_icon(app.handle(), ICON);
            let control = tauri::async_runtime::block_on(clappkit::connect_or_die(CLI));
            let core = Core::new();
            let ipc_core = core.clone();
            let startup_core = core.clone();
            let startup_app = app.handle().clone();
            clappkit::app::spawn_ipc(
                app.handle().clone(),
                CLI,
                window_policy(),
                move |req, _caller| {
                    let core = ipc_core.clone();
                    async move { core.handle_cli(req).await }
                },
            );
            app.manage(core);
            app.manage(control);
            tauri::async_runtime::spawn(async move {
                let snapshot = startup_core.bootstrap().await;
                clappkit::app::push_state(&startup_app, snapshot);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                clappkit::app::hide_window(window.app_handle());
            }
        })
        .invoke_handler(tauri::generate_handler![run_cmd])
        .run(tauri::generate_context!())
        .expect("Quayt Tauri runtime failed");
}
