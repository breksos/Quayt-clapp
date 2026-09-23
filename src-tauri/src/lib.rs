#[cfg(not(test))]
pub mod app;
pub mod cli;
pub mod state;

pub const APP_ID: &str = "com.arfium.quayt";
pub const CLI: &str = "quayt";

#[cfg(not(test))]
pub fn main_dispatch() {
    clappkit::role::main_dispatch(APP_ID, CLI, cli::run, app::run);
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identity_is_fixed_for_phase_one() {
        assert_eq!(APP_ID, "com.arfium.quayt");
        assert_eq!(CLI, "quayt");
    }
}
