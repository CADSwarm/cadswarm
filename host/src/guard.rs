//! One host per data folder.
//!
//! Two `cadswarm-host run` processes on the same folder share one identity, so each rejoin
//! supersedes the other's fence: the loser keeps running while every reducer call is refused, and
//! whichever process finishes an export decides whether the design is indexed at all. That failure
//! is silent from the outside, so refuse the second start with an explanation instead.

use anyhow::{Context, Result};
use std::fs::{File, OpenOptions, TryLockError};
use std::io::Write;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub struct InstanceGuard {
    file: File,
    owner: PathBuf,
}

impl InstanceGuard {
    /// Take the folder's lock, or say who holds it.
    pub fn acquire(data_dir: &Path) -> Result<Self> {
        let path = data_dir.join("host.lock");
        // Windows locks a whole file, so the holder cannot be read out of the locked file itself;
        // the description lives beside it and is only decoration (the lock is the authority).
        let owner = data_dir.join("host.owner");
        let mut file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&path)
            .with_context(|| format!("opening {}", path.display()))?;
        match file.try_lock() {
            Ok(()) => {}
            Err(TryLockError::WouldBlock) => {
                let holder = holder(&owner);
                anyhow::bail!(
                    "another cadswarm-host is already running on {} ({holder}). Stop it first: two \
                     hosts on one database share an identity, supersede each other's fence, and the \
                     one that loses the export leaves its design out of Elastic. Lock file: {}",
                    data_dir.display(),
                    path.display()
                );
            }
            Err(TryLockError::Error(e)) => {
                return Err(anyhow::Error::new(e).context(format!("locking {}", path.display())))
            }
        }
        let _ = writeln!(file, "pid {}", std::process::id());
        file.flush().ok();
        let _ = std::fs::write(&owner, format!("pid {} exe {}", std::process::id(), exe()));
        Ok(Self { file, owner })
    }
}

/// What the process holding the lock says about itself.
fn holder(owner: &Path) -> String {
    let text = std::fs::read_to_string(owner).unwrap_or_default();
    let text = text.trim();
    if text.is_empty() {
        "an earlier process".into()
    } else {
        text.into()
    }
}

fn exe() -> String {
    std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default()
}

impl Drop for InstanceGuard {
    fn drop(&mut self) {
        // Clear the description and release the lock; the lock file itself can stay and be reused.
        let _ = std::fs::remove_file(&self.owner);
        let _ = self.file.unlock();
    }
}

#[cfg(test)]
mod tests {
    use super::InstanceGuard;
    use std::path::PathBuf;

    fn folder(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("cadswarm-guard-{}-{tag}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_second_host_on_the_same_folder_is_refused_and_named() {
        let dir = folder("second");
        let _first = InstanceGuard::acquire(&dir).unwrap();
        let err = InstanceGuard::acquire(&dir).unwrap_err().to_string();
        assert!(err.contains("another cadswarm-host is already running"), "{err}");
        assert!(err.contains(&format!("pid {}", std::process::id())), "{err}");
        assert!(err.contains("host.lock"), "{err}");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_lock_is_released_when_the_host_stops() {
        let dir = folder("release");
        {
            let _guard = InstanceGuard::acquire(&dir).unwrap();
        }
        assert!(InstanceGuard::acquire(&dir).is_ok(), "a stopped host must not block the next one");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn two_folders_run_independently() {
        let (a, b) = (folder("a"), folder("b"));
        let _guard = InstanceGuard::acquire(&a).unwrap();
        assert!(InstanceGuard::acquire(&b).is_ok(), "different folders must not block each other");
        std::fs::remove_dir_all(&a).ok();
        std::fs::remove_dir_all(&b).ok();
    }
}
