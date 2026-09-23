use crate::CLI;
use serde_json::{json, Value};

pub const PUBLIC_COMMANDS: [&str; 5] = ["status", "show", "hide", "quit", "ping"];

pub async fn run(args: Vec<String>) {
    let req = match request_from_args(&args) {
        Ok(Some(req)) => req,
        Ok(None) => {
            print_help();
            return;
        }
        Err(error) => {
            eprintln!("{CLI}: {error}");
            print_help();
            std::process::exit(2);
        }
    };
    let req = attach_agent(req);
    match clappkit::ipc::request(CLI, &req).await {
        Ok(resp) if resp.get("ok").and_then(Value::as_bool) == Some(false) => {
            let message = resp
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("command failed");
            eprintln!("{CLI}: {message}");
            std::process::exit(1);
        }
        Ok(resp) => println!(
            "{}",
            serde_json::to_string_pretty(&resp).unwrap_or_else(|_| resp.to_string())
        ),
        Err(error) => {
            eprintln!("{CLI}: {error}");
            std::process::exit(1);
        }
    }
}

fn request_from_args(args: &[String]) -> Result<Option<Value>, String> {
    let Some(command) = args.first().map(String::as_str) else {
        return Ok(Some(json!({"cmd":"status"})));
    };
    match command {
        "-h" | "--help" | "help" => {
            expect_len(args, command)?;
            Ok(None)
        }
        command if PUBLIC_COMMANDS.contains(&command) => {
            expect_len(args, command)?;
            Ok(Some(json!({"cmd":command})))
        }
        other => Err(format!("unknown command '{other}'")),
    }
}

fn attach_agent(mut req: Value) -> Value {
    let Ok(agent) = std::env::var("CLATCH_AGENT_ID") else {
        return req;
    };
    if !agent.trim().is_empty() {
        req.as_object_mut()
            .unwrap()
            .insert("agent".into(), Value::String(agent));
    }
    req
}

fn expect_len(args: &[String], command: &str) -> Result<(), String> {
    if args.len() == 1 {
        Ok(())
    } else {
        Err(format!("'{command}' takes no arguments"))
    }
}

fn print_help() {
    println!("Quayt CLI\n\nUsage:\n  quayt status\n  quayt show\n  quayt hide\n  quayt quit\n  quayt ping");
}

#[cfg(test)]
mod tests {
    use super::*;
    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }
    #[test]
    fn public_surface_has_exactly_five_commands() {
        assert_eq!(PUBLIC_COMMANDS, ["status", "show", "hide", "quit", "ping"]);
        for command in PUBLIC_COMMANDS {
            assert!(request_from_args(&args(&[command])).unwrap().is_some());
        }
    }
    #[test]
    fn internal_and_operational_commands_are_not_public() {
        for command in ["state", "run", "login", "signal", "raw", "close", "focus"] {
            assert!(request_from_args(&args(&[command])).is_err());
        }
    }
}
