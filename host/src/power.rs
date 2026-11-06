//! Keep a hosted team running while its window is minimised.
//!
//! Windows throttles background work (EcoQoS) and can sleep the machine when a build is waiting on a
//! solver or an AI helper. Both look like "it stopped when I minimised it". The host opts out of the
//! throttling and holds the system awake for as long as a team runs; the state is released when the
//! process exits. No effect outside Windows.
//!
//! On a Modern Standby laptop the system sleeps when the screen turns off (3 min idle on battery), and
//! ES_SYSTEM_REQUIRED does not stop that: a brief froze twice for 45-80 min with every agent mid-task.
//! So while agents are working the screen is held on too; once the team is idle only the system is.

use std::sync::atomic::{AtomicBool, Ordering};

static BUSY: AtomicBool = AtomicBool::new(false);

/// Say whether agents are working; the keep-awake thread holds the screen on while they are.
pub fn set_busy(busy: bool) {
    BUSY.store(busy, Ordering::Relaxed);
}

pub fn is_busy() -> bool {
    BUSY.load(Ordering::Relaxed)
}

/// Opt this process out of Windows' background throttling and keep the system awake.
pub fn keep_running_in_background() {
    #[cfg(windows)]
    {
        unthrottle();
        // ES_CONTINUOUS applies to the calling thread, so a thread that stays alive re-asserts it.
        // Every 10 s, well inside the shortest screen timeout, so a brief that starts is covered at once.
        std::thread::Builder::new()
            .name("cadswarm-keep-awake".into())
            .spawn(|| loop {
                set_awake(is_busy());
                std::thread::sleep(std::time::Duration::from_secs(10));
            })
            .ok();
    }
}

#[cfg(windows)]
fn set_awake(display: bool) {
    use std::ffi::c_void;
    use std::mem::size_of;

    const ES_CONTINUOUS: u32 = 0x8000_0000;
    const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;
    const ES_DISPLAY_REQUIRED: u32 = 0x0000_0002;
    const PROCESS_POWER_THROTTLING: i32 = 4;
    const PROCESS_POWER_THROTTLING_CURRENT_VERSION: u32 = 1;
    const PROCESS_POWER_THROTTLING_EXECUTION_SPEED: u32 = 0x1;

    #[repr(C)]
    struct ThrottlingState {
        version: u32,
        control_mask: u32,
        state_mask: u32,
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn SetThreadExecutionState(flags: u32) -> u32;
        fn GetCurrentProcess() -> isize;
        fn SetProcessInformation(process: isize, class: i32, info: *mut c_void, size: u32) -> i32;
    }

    let flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | if display { ES_DISPLAY_REQUIRED } else { 0 };
    unsafe {
        SetThreadExecutionState(flags);
    }
    // Control the execution-speed class but clear the bit: 0 = never throttled.
    let mut state = ThrottlingState {
        version: PROCESS_POWER_THROTTLING_CURRENT_VERSION,
        control_mask: PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
        state_mask: 0,
    };
    unsafe {
        SetProcessInformation(
            GetCurrentProcess(),
            PROCESS_POWER_THROTTLING,
            &mut state as *mut ThrottlingState as *mut c_void,
            size_of::<ThrottlingState>() as u32,
        );
    }
}

#[cfg(windows)]
fn unthrottle() {
    set_awake(is_busy());
}

#[cfg(test)]
mod tests {
    #[test]
    fn calling_it_is_harmless_on_any_platform() {
        super::keep_running_in_background();
    }

    #[test]
    fn the_busy_flag_follows_the_team() {
        super::set_busy(true);
        assert!(super::is_busy());
        super::set_busy(false);
        assert!(!super::is_busy());
    }
}
