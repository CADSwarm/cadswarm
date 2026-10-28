//! Keep a hosted team running while its window is minimised.
//!
//! Windows throttles background work (EcoQoS) and can sleep the machine when a build is waiting on a
//! solver or an AI helper. Both look like "it stopped when I minimised it". The host opts out of the
//! throttling and holds the system awake for as long as a team runs; the state is released when the
//! process exits. No effect outside Windows.

/// Opt this process out of Windows' background throttling and keep the system awake.
pub fn keep_running_in_background() {
    #[cfg(windows)]
    {
        unthrottle();
        // ES_CONTINUOUS applies to the calling thread, so a thread that stays alive re-asserts it.
        std::thread::Builder::new()
            .name("cadswarm-keep-awake".into())
            .spawn(|| loop {
                set_awake();
                std::thread::sleep(std::time::Duration::from_secs(30));
            })
            .ok();
    }
}

#[cfg(windows)]
fn set_awake() {
    use std::ffi::c_void;
    use std::mem::size_of;

    const ES_CONTINUOUS: u32 = 0x8000_0000;
    const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;
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

    unsafe {
        SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED);
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
    set_awake();
}

#[cfg(test)]
mod tests {
    #[test]
    fn calling_it_is_harmless_on_any_platform() {
        super::keep_running_in_background();
    }
}
