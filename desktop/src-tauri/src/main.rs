// OpenConstructionERP Desktop, Tauri v2 application.
//
// Manages the FastAPI backend as a sidecar process.
// The React frontend loads in a native webview window.
// Backend communicates via http://localhost:{port}/api/
//
// Robustness contract (why this file is defensive):
//   In release builds the process has no console (windows_subsystem = "windows"),
//   so any panic dies silently and, if it happens inside setup(), the window
//   never appears. That is exactly the "I click the icon and nothing happens"
//   failure. So setup() must NEVER panic: every fallible step is handled, the
//   splash window is kept open, a human-readable error is shown via setError(),
//   and a full diagnostic log is always written to
//   ~/.openestimate/desktop-launcher.log (alongside the backend's own data).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{
    menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Asks GitHub, once per start, whether a newer release exists, and offers it in
/// the failure window. Kept in its own file because it must be able to speak
/// when nothing else in this one worked.
mod update_check;

struct AppState {
    /// Handle to the spawned backend process so it survives past setup() and
    /// can be killed when the app exits.
    backend_child: Mutex<Option<CommandChild>>,
    /// The local URL the app is served on (e.g. http://127.0.0.1:8732/).
    ///
    /// Resolved once the backend is healthy and the webview is pointed at it.
    /// Stored here so the tray menu and the "open in your browser" command can
    /// hand the user the exact same address the app window is showing, even
    /// though the port is chosen dynamically at startup.
    app_url: Mutex<Option<String>>,
    /// Set the moment the app decides to exit, before the sidecar is stopped.
    ///
    /// Everything that watches the backend has to be able to tell a crash from
    /// a shutdown we asked for. Without this flag the watchers below would see
    /// the very kill we just issued and announce to a user who is closing the
    /// app that their backend has died.
    shutting_down: Arc<AtomicBool>,
    /// Set by the output pump when the sidecar process is observed to exit, so
    /// the exit path can wait for the process to really be gone rather than
    /// assume it.
    backend_exited: Arc<AtomicBool>,
    /// Set by whichever watcher first told the user the backend was lost, so
    /// the same failure is never reported twice through two channels.
    backend_lost_reported: Arc<AtomicBool>,
    /// The loopback port of the sidecar THIS launcher started.
    ///
    /// Set only on the spawn path. On the attach path the backend belongs to
    /// somebody else - a developer running `serve` in a terminal - and asking
    /// it to shut down would stop a server we were only borrowing.
    backend_port: Mutex<Option<u16>>,
    /// Secret the backend requires before it will stop itself, one per run.
    ///
    /// Generated here, handed to the sidecar in its environment, and sent back
    /// in a header on the way out. It never touches the disk. See
    /// `backend/app/core/desktop_shutdown.py` for the guards it satisfies.
    shutdown_token: String,
}

/// How long to give the platform opener a chance to report failure.
///
/// `cmd /c start` and `open` hand the target to the OS and exit within a few
/// milliseconds, so a real failure lands well inside this window. `xdg-open`
/// may exec the browser in place instead and stay alive for the whole desktop
/// session, which is why the opener is never simply waited on: that would block
/// the caller until the user closed their browser. Polling for an early
/// non-zero exit catches the failures the OS does report and returns
/// immediately on the normal path.
const OPENER_FAILURE_WINDOW: Duration = Duration::from_millis(400);
const OPENER_POLL_INTERVAL: Duration = Duration::from_millis(20);

/// Start the platform opener for a URL or file path.
///
/// Uses the platform opener directly (`cmd /c start` on Windows, `open` on
/// macOS, `xdg-open` on Linux) rather than the tauri shell plugin's deprecated
/// `open`: it is fully cross-platform and adds no dependency.
///
/// Split per platform by `cfg` attribute rather than by a runtime `cfg!`
/// branch inside one body, because the Windows arm needs `CommandExt`, which
/// only exists on Windows.
#[cfg(target_os = "windows")]
fn spawn_os_opener(target: &str) -> std::io::Result<std::process::Child> {
    use std::os::windows::process::CommandExt;

    /// `CREATE_NO_WINDOW`. A console subsystem process spawned from a GUI app
    /// allocates and shows its own console, so without this every outbound
    /// link put a black command window on screen ahead of the browser. It is
    /// the launcher's window, not the browser's, and it is what made clicking
    /// a link in the app look like it opened a separate window rather than a
    /// page.
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    // cmd.exe re-parses the command line it is handed, and Rust quotes an
    // argument only when it contains a space or a tab. A target with neither,
    // such as https://example.invalid/&calc, therefore arrives unquoted and
    // cmd reads the & as a command separator: the second half runs as a
    // program. The caller's scheme check does not stop it, because the string
    // does begin with https://. So the target is quoted here, at the one place
    // that builds the command line, rather than trusted to arrive safe.
    //
    // raw_arg rather than arg: Rust's own escaping would turn the quotes into
    // \" for a program that parses its command line the C way, and cmd does
    // not, so the quotes have to be written literally.
    //
    // The empty "" is start's title argument; without it a quoted target is
    // mis-parsed as the window title and nothing opens.
    if !target_is_safe_for_cmd(target) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "The link contains characters that cannot be passed to the shell safely",
        ));
    }

    let mut command = std::process::Command::new("cmd");
    for piece in cmd_open_args(target) {
        command.raw_arg(piece);
    }
    command.creation_flags(CREATE_NO_WINDOW).spawn()
}

/// The literal command-line pieces handed to cmd.exe to open one target.
///
/// Split out from the spawn so the quoting can be asserted rather than trusted.
/// A test that only exercises `target_is_safe_for_cmd` would stay green if the
/// quotes came back off, because refusing a quote character and quoting the
/// target are two different halves of one guard and only the first has an
/// obvious unit to test.
///
/// Args:
///     target: A URL or path that `target_is_safe_for_cmd` has already accepted.
///
/// Returns:
///     The four pieces, each written verbatim onto the command line.
#[cfg(target_os = "windows")]
fn cmd_open_args(target: &str) -> [String; 4] {
    [
        "/c".to_string(),
        "start".to_string(),
        // start's title argument. Without it a quoted target is read as the
        // window title and nothing opens.
        "\"\"".to_string(),
        format!("\"{target}\""),
    ]
}

/// Whether a target may be placed inside double quotes on a cmd.exe line.
///
/// Quoting neutralises the separators (`&`, `|`, `<`, `>`) and the escape
/// character (`^`), so the only characters that still matter are the ones that
/// can break out of the quoting itself: a double quote closes it, and a
/// carriage return or newline ends the line. None of the three can appear in a
/// URL that reached us honestly (they are percent-encoded there) or in a
/// Windows path, where the double quote is not a legal filename character, so
/// refusing them costs nothing a real caller wanted.
///
/// Deliberately NOT refused: `%`. cmd expands `%NAME%` even inside quotes, so a
/// URL carrying a defined variable name between percent signs would open the
/// wrong address. That is a wrong-link bug and not a way to run a program, and
/// percent signs are how every encoded character in a URL is spelled, so
/// refusing them would break far more links than it could protect.
///
/// Args:
///     target: The URL or path about to be handed to the opener.
///
/// Returns:
///     True when the target can be quoted safely.
#[cfg(target_os = "windows")]
fn target_is_safe_for_cmd(target: &str) -> bool {
    !target.contains('"') && !target.contains('\r') && !target.contains('\n')
}

#[cfg(target_os = "macos")]
fn spawn_os_opener(target: &str) -> std::io::Result<std::process::Child> {
    std::process::Command::new("open").arg(target).spawn()
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn spawn_os_opener(target: &str) -> std::io::Result<std::process::Child> {
    std::process::Command::new("xdg-open").arg(target).spawn()
}

/// Open a URL or file path in the operating system's default handler, and say
/// so honestly when the operating system refuses.
///
/// For a URL this lands the user in their normal web browser at the local
/// address. Spawning alone proves nothing: it succeeds the moment the launcher
/// process starts, so a machine with no registered browser, a broken file
/// association or no `xdg-open` handler used to be reported back as a success
/// and the user was told nothing at all.
///
/// This is not a complete detector, and callers should not present it as one.
/// Windows `start` still exits 0 when it puts up the "How do you want to open
/// this file?" chooser, so `Ok` here means the OS accepted the request, not
/// that a browser window appeared.
fn open_with_os_default(target: &str) -> Result<(), String> {
    let mut child = spawn_os_opener(target).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + OPENER_FAILURE_WINDOW;
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(status)) => {
                return Err(match status.code() {
                    Some(code) => format!("the system opener exited with code {code}"),
                    None => "the system opener was terminated".to_string(),
                })
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    // Still running past the window: it has taken ownership of
                    // the target (the normal xdg-open case) and there is
                    // nothing left to report.
                    return Ok(());
                }
                std::thread::sleep(OPENER_POLL_INTERVAL);
            }
            Err(e) => return Err(e.to_string()),
        }
    }
}

/// Open the running app in the user's default web browser.
///
/// Exposed to the splash first-run card and to any in-app control (via
/// `withGlobalTauri`). Reads the resolved local URL from `AppState`; if startup
/// has not gotten that far yet it returns a friendly error the caller can show.
///
/// `path` lets the in-app toolbar open the EXACT page the user is on rather
/// than just the home page. It is treated as a path within the app (for example
/// "/boq" or "/projects/123/finance"); anything that is not a clean local path
/// is ignored and the home page is opened, so a caller can never be redirected
/// somewhere off the local origin.
#[tauri::command]
fn open_app_in_browser(app: tauri::AppHandle, path: Option<String>) -> Result<(), String> {
    let base = {
        let state = app.state::<AppState>();
        let guard = state.app_url.lock().unwrap();
        guard.clone()
    };
    let base = base.ok_or_else(|| {
        "The app is still starting. Please try again in a moment.".to_string()
    })?;
    let url = build_local_url(&base, path.as_deref());
    open_with_os_default(&url).map_err(|e| format!("Could not open your browser: {e}"))
}

/// Open an arbitrary external link (http/https/mailto) in the OS default handler.
///
/// The in-app UI carries many outbound links - the docs, the GitHub repo, the
/// marketing site, contact mail. Inside the webview a `target="_blank"` anchor is
/// swallowed and nothing opens, so the frontend routes every external-link click
/// here. Only web and mail schemes are honoured, so a stray or crafted href
/// cannot name a local program for the opener to launch.
///
/// That scheme test is not on its own what makes this safe, and it used to be
/// described as though it were. It bounds what the opener is asked to open; it
/// says nothing about how the string survives the shell on the way there, and
/// on Windows the opener goes through cmd.exe. https://example.invalid/&calc
/// passes this check in full. The quoting in `spawn_os_opener` is what stops
/// the tail of that string being run as a second command, and the two together
/// are the guard.
#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let target = url.trim();
    let lower = target.to_ascii_lowercase();
    let allowed = lower.starts_with("http://")
        || lower.starts_with("https://")
        || lower.starts_with("mailto:");
    if !allowed {
        return Err("Only http, https and mailto links can be opened".to_string());
    }
    open_with_os_default(target).map_err(|e| format!("Could not open the link: {e}"))
}

/// Combine the resolved local base URL with a caller-supplied app path.
///
/// Only same-origin paths are honoured: the path must start with a single "/"
/// (not "//", which a browser reads as a protocol-relative host) and must not
/// contain a scheme. Anything else falls back to the bare base URL. This keeps
/// the "open in browser" action firmly on the local app and never lets a path
/// argument send the user to an arbitrary site.
fn build_local_url(base: &str, path: Option<&str>) -> String {
    let trimmed = base.trim_end_matches('/');
    match path {
        Some(p)
            if p.starts_with('/')
                && !p.starts_with("//")
                && !p.contains("://")
                && !p.contains('\\') =>
        {
            format!("{trimmed}{p}")
        }
        _ => format!("{trimmed}/"),
    }
}

/// Return the resolved local URL the app is served on, for the UI to display or
/// open. Empty string until the backend is healthy and the URL is known.
#[tauri::command]
fn get_app_url(app: tauri::AppHandle) -> String {
    let state = app.state::<AppState>();
    let guard = state.app_url.lock().unwrap();
    guard.clone().unwrap_or_default()
}

/// Open the launcher diagnostic log in the OS default handler.
///
/// Exposed to the splash screen (via `withGlobalTauri`) so the failure UI can
/// offer a one-click "Open log" button. Returns an error string the splash can
/// show if the log path cannot be resolved or opened. Shares `open_with_os_default`
/// with the browser and link commands rather than repeating the platform block:
/// this is the surface a user reaches for when something has already gone
/// wrong, so it is the last place that should quietly claim success.
#[tauri::command]
fn open_log_file(_app: tauri::AppHandle) -> Result<(), String> {
    let path = log_path().ok_or_else(|| "Could not resolve the log file path".to_string())?;
    let path_str = path.to_string_lossy().to_string();
    open_with_os_default(&path_str).map_err(|e| format!("Could not open the log file: {e}"))
}

/// Resolve the user's home directory without pulling in extra crates.
fn home_dir() -> Option<PathBuf> {
    for var in ["USERPROFILE", "HOME"] {
        if let Ok(p) = std::env::var(var) {
            if !p.is_empty() {
                return Some(PathBuf::from(p));
            }
        }
    }
    None
}

/// Path of the launcher diagnostic log (same folder the backend uses for data).
fn log_path() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".openestimate").join("desktop-launcher.log"))
}

/// Append one line to the diagnostic log (best effort) and to stderr.
///
/// This is the single most important diagnostic when a user reports "nothing
/// happens": even if the window never paints, the log records how far startup
/// got and the exact error.
fn log_line(msg: &str) {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let line = format!("[{secs}] {msg}\n");

    if let Some(path) = log_path() {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
        {
            let _ = f.write_all(line.as_bytes());
        }
    }
    eprintln!("{}", line.trim_end());
}

/// Escape a string for embedding inside a single-quoted JavaScript literal.
fn js_escape(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('\'', "\\'")
        .replace('\n', " ")
        .replace('\r', " ")
}

/// Run a snippet of JavaScript in the main window, retried a few times.
///
/// setup() may run before the page has finished loading its inline script, so
/// we retry the eval over ~2 seconds. Every snippet sent through here must
/// therefore be idempotent, because it will run up to eight times.
///
/// `raise_window` brings the window to the front on each attempt. That is right
/// while the splash is up and the window is what the user just asked for, and
/// wrong afterwards: an app sitting in the tray would be yanked onto the screen
/// by a message that could have waited.
fn eval_retrying(handle: &tauri::AppHandle, js: String, raise_window: bool) {
    let handle = handle.clone();
    tauri::async_runtime::spawn(async move {
        for _ in 0..8 {
            if let Some(window) = handle.get_webview_window("main") {
                if raise_window {
                    let _ = window.show();
                }
                let _ = window.eval(&js);
            }
            tokio::time::sleep(std::time::Duration::from_millis(250)).await;
        }
    });
}

/// Run a snippet in the splash window. The splash boot functions are idempotent
/// (they just set DOM state), so repeated calls are harmless.
fn eval_in_splash(handle: &tauri::AppHandle, js: String) {
    eval_retrying(handle, js, true);
}

/// Tell the splash where the diagnostic log lives so a failure message can point
/// the user straight at it.
fn report_log_path(handle: &tauri::AppHandle) {
    if let Some(path) = log_path() {
        let p = js_escape(&path.to_string_lossy());
        eval_in_splash(
            handle,
            format!("(function(){{if(typeof setLogPath==='function'){{setLogPath('{p}');}}}})()"),
        );
    }
}

/// Tell the splash which build it is, so a startup failure carries its version.
///
/// This is the one screen a user with a backend that will not start can still
/// read, and the version is the first thing anyone answering their report has to
/// know: the same message is produced by faults that were fixed releases ago and
/// by faults that are still open. Without it a report cannot be triaged at all.
/// Every other place that names the version lives behind a running application,
/// which is precisely what these users do not have.
fn report_app_version(handle: &tauri::AppHandle) {
    let v = js_escape(env!("CARGO_PKG_VERSION"));
    eval_in_splash(
        handle,
        format!("(function(){{if(typeof setAppVersion==='function'){{setAppVersion('{v}');}}}})()"),
    );
}

/// Advance one step of the visible boot checklist on the splash screen.
///
/// `status` is one of "pending" | "active" | "done" | "failed". Never panics;
/// if the splash is not ready yet the retrying eval picks it up shortly.
fn boot_stage(handle: &tauri::AppHandle, id: &str, status: &str, detail: &str) {
    let id = js_escape(id);
    let status = js_escape(status);
    let detail = js_escape(detail);
    eval_in_splash(
        handle,
        format!(
            "(function(){{if(typeof bootStage==='function'){{bootStage('{id}','{status}','{detail}');}}}})()"
        ),
    );
}

/// Show a fatal error on the splash screen and mark a checklist step as failed,
/// without ever panicking. Always pairs the message with the log path so the
/// user can find the full diagnostics.
fn report_fatal_stage(handle: &tauri::AppHandle, stage: &str, message: &str) {
    log_line(&format!("FATAL [{stage}]: {message}"));
    report_log_path(handle);
    report_app_version(handle);
    // Every way startup can fail comes through here, so this is the one place
    // that has to carry the offer of a newer version. For a user whose
    // installed build cannot start at all, that sentence is the entire fix, and
    // the application's own update notice can never reach them: it is served by
    // the backend that just failed. Adds nothing to the failure path but a flag
    // read, and shows nothing unless an answer has already come back.
    update_check::note_startup_failed(handle, env!("CARGO_PKG_VERSION"));
    let stage_js = js_escape(stage);
    let msg = js_escape(message);
    eval_in_splash(
        handle,
        format!(
            "(function(){{\
                if(typeof failStage==='function'){{failStage('{stage_js}','{msg}');}}\
                else if(typeof setError==='function'){{setError('{msg}');}}\
            }})()"
        ),
    );
}

/// Tell the user the backend is gone, in whatever page the window is showing.
///
/// The splash reporting above is unusable once startup has succeeded. The
/// moment the webview navigates to the application the splash document is torn
/// down with every function the launcher talks to it through, so `failStage`
/// and `setError` are no longer defined and the `typeof` guards turn every
/// later report into a silent no-op. A backend that died an hour into the
/// session therefore had no way at all to reach the person using it: the window
/// kept showing the last screen it had rendered while every action on it failed.
///
/// So this builds its own overlay out of plain DOM instead of calling into the
/// page, which works on the splash and on the application alike, and needs
/// nothing from the frontend bundle. It is idempotent by element id because
/// `eval_retrying` will run it up to eight times.
fn report_backend_lost(
    handle: &tauri::AppHandle,
    reported: &AtomicBool,
    headline: &str,
    detail: &str,
) {
    // First reporter wins. The process pump and the liveness watch can both
    // see the same death, and the user should hear about it once.
    if reported
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return;
    }

    log_line(&format!("BACKEND LOST: {headline} {detail}"));
    let log_hint = log_path()
        .map(|p| format!(" The launcher log is at {}.", p.display()))
        .unwrap_or_default();
    let head_js = js_escape(headline);
    let body_js = js_escape(&format!("{detail}{log_hint}"));

    eval_retrying(
        handle,
        format!(
            "(function(){{\
                var d=document;\
                if(!d||d.getElementById('oe-backend-lost')){{return;}}\
                var host=d.body||d.documentElement;\
                if(!host){{return;}}\
                var o=d.createElement('div');\
                o.id='oe-backend-lost';\
                o.setAttribute('style','position:fixed;top:0;left:0;right:0;bottom:0;\
z-index:2147483647;background:rgba(15,17,21,0.94);color:#f5f7fa;display:flex;\
align-items:center;justify-content:center;padding:32px;text-align:left;\
font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;\
line-height:1.55');\
                var c=d.createElement('div');\
                c.setAttribute('style','max-width:620px');\
                var h=d.createElement('div');\
                h.setAttribute('style','font-size:20px;font-weight:600;margin-bottom:12px');\
                h.textContent='{head_js}';\
                var p=d.createElement('div');\
                p.textContent='{body_js}';\
                c.appendChild(h);c.appendChild(p);o.appendChild(c);host.appendChild(o);\
            }})()"
        ),
        false,
    );
}

/// Show or clear the notice that says the backend has gone quiet.
///
/// Deliberately not the modal above. Silence is a symptom that can end: a long
/// import holding the database pool keeps the health check waiting on a backend
/// that is working perfectly well, and telling that person their app is dead,
/// behind a sheet they cannot dismiss, would cost them the very work that
/// caused the delay. So this is a strip along the bottom that takes no clicks
/// (`pointer-events:none`) and is removed again the moment the backend answers.
fn set_backend_silent_notice(handle: &tauri::AppHandle, shown: bool) {
    let js = if shown {
        let text = js_escape(
            "The application backend has not answered for about two minutes. It may be working \
through a long operation, such as a large import, and this notice will disappear as soon as it \
responds. If the window stays unusable, close it and start OpenConstructionERP again.",
        );
        format!(
            "(function(){{\
                var d=document;\
                if(!d||d.getElementById('oe-backend-silent')){{return;}}\
                var host=d.body||d.documentElement;\
                if(!host){{return;}}\
                var o=d.createElement('div');\
                o.id='oe-backend-silent';\
                o.setAttribute('style','position:fixed;left:0;right:0;bottom:0;\
z-index:2147483646;pointer-events:none;background:rgba(15,17,21,0.94);color:#f5f7fa;\
padding:14px 20px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;\
font-size:14px;line-height:1.5');\
                o.textContent='{text}';\
                host.appendChild(o);\
            }})()"
        )
    } else {
        "(function(){\
            var d=document;if(!d){return;}\
            var e=d.getElementById('oe-backend-silent');\
            if(e&&e.parentNode){e.parentNode.removeChild(e);}\
        })()"
            .to_string()
    };
    eval_retrying(handle, js, false);
}

/// How often the liveness watch asks the backend whether it is still there.
const LIVENESS_POLL_INTERVAL: Duration = Duration::from_secs(10);
/// How long one liveness probe may take before it counts as unanswered.
const LIVENESS_PROBE_TIMEOUT: Duration = Duration::from_secs(5);
/// Consecutive refused connections before the backend is called dead. A refusal
/// is an immediate and unambiguous answer from the operating system: nothing is
/// listening on that port any more.
const LIVENESS_REFUSED_STRIKES: u32 = 2;
/// Consecutive unanswered probes before the user is told the backend has gone
/// quiet. Silence is much weaker evidence than a refusal, because a machine
/// waking from sleep, a heavy import or a long migration can all keep a request
/// waiting, so this threshold is deliberately several times longer and what it
/// triggers is a reversible notice rather than a verdict. Two minutes is not
/// generous by accident: the health check takes a database connection, and a
/// large import holding the pool keeps every probe waiting on a backend that is
/// working perfectly well.
const LIVENESS_SILENT_STRIKES: u32 = 12;

/// Keep watching the backend AFTER it has answered its first health check.
///
/// Readiness was the end of the launcher's attention: past that point nothing
/// asked whether the backend was still alive. When it went away the window
/// stayed exactly as it was, so a user went on clicking a screen whose every
/// request was failing, with no message anywhere saying why.
///
/// This is also the only cover for the attach path, where the backend belongs
/// to another process entirely: there is no child to wait on and no output to
/// pump, so its death is invisible by construction.
async fn watch_backend_liveness(
    handle: tauri::AppHandle,
    port: u16,
    shutting_down: Arc<AtomicBool>,
    reported: Arc<AtomicBool>,
) {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{port}/api/health");
    let mut refused: u32 = 0;
    let mut silent: u32 = 0;
    let mut silent_notice = false;

    loop {
        tokio::time::sleep(LIVENESS_POLL_INTERVAL).await;
        // A shutdown we asked for is not a failure, and a death already
        // reported by the process pump does not need saying twice.
        if shutting_down.load(Ordering::SeqCst) || reported.load(Ordering::SeqCst) {
            return;
        }

        match client.get(&url).timeout(LIVENESS_PROBE_TIMEOUT).send().await {
            // Any answer at all, including an error status, proves the process
            // is alive and serving. Only absence is evidence of death here.
            Ok(_) => {
                refused = 0;
                silent = 0;
                if silent_notice {
                    silent_notice = false;
                    log_line("liveness: the backend is answering again");
                    set_backend_silent_notice(&handle, false);
                }
            }
            Err(e) if e.is_connect() => {
                refused += 1;
                silent = 0;
                log_line(&format!(
                    "liveness: connection to the backend refused ({}/{})",
                    refused, LIVENESS_REFUSED_STRIKES
                ));
            }
            Err(e) => {
                silent += 1;
                log_line(&format!(
                    "liveness: backend did not answer ({}/{}): {e}",
                    silent, LIVENESS_SILENT_STRIKES
                ));
            }
        }

        if refused >= LIVENESS_REFUSED_STRIKES {
            if silent_notice {
                set_backend_silent_notice(&handle, false);
            }
            report_backend_lost(
                &handle,
                &reported,
                "The application backend has stopped",
                "Nothing is listening on the local address any more, so this window can no \
longer load or save anything. Please close it and start OpenConstructionERP again. If this \
keeps happening, send the log file to info@datadrivenconstruction.io.",
            );
            return;
        }
        // Silence is not a verdict, so the watch does not end here. It says
        // what it sees, keeps polling, and takes the notice down again if the
        // backend comes back. Only a refusal, above, is final.
        if silent >= LIVENESS_SILENT_STRIKES && !silent_notice {
            silent_notice = true;
            log_line("liveness: the backend has gone quiet, telling the user");
            set_backend_silent_notice(&handle, true);
        }
    }
}

/// Parse a backend ``STAGE:<id>:<status>[:<detail>]`` marker line.
///
/// Returns ``Some((id, splash_status, detail))`` where splash_status is mapped
/// to the values the splash checklist understands. Returns ``None`` for lines
/// that are not stage markers.
fn parse_stage_marker(line: &str) -> Option<(String, String, String)> {
    let rest = line.trim().strip_prefix("STAGE:")?;
    let mut parts = rest.splitn(3, ':');
    let id = parts.next()?.trim().to_string();
    let raw_status = parts.next()?.trim().to_string();
    let detail = parts.next().unwrap_or("").trim().to_string();
    if id.is_empty() || raw_status.is_empty() {
        return None;
    }
    let splash_status = match raw_status.as_str() {
        "start" | "progress" => "active",
        "done" => "done",
        "fail" => "failed",
        _ => "active",
    }
    .to_string();
    Some((id, splash_status, detail))
}

/// Accumulates a Python traceback seen on the sidecar's stderr so the launcher
/// can report the real exception line as the failure cause when the backend
/// crashed too early to emit a `STAGE:server:fail` marker. Only the exception
/// summary line is kept (chained tracebacks overwrite it, which is what Python
/// prints last and what the user needs to see), so the database-shutdown noise
/// that follows a crash can never become the reported cause.
#[derive(Default)]
struct TracebackCapture {
    capturing: bool,
    cause: Option<String>,
}

impl TracebackCapture {
    /// Feed one stderr line (the caller has already split on `\n`).
    fn feed_line(&mut self, raw: &str) {
        let line = raw.trim_end();
        if line.contains("Traceback (most recent call last)") {
            self.capturing = true;
            return;
        }
        if !self.capturing {
            return;
        }
        let body = line.trim();
        if body.is_empty() {
            return;
        }
        // Stack-frame lines are indented; keep reading until the summary line.
        if line.starts_with(' ') || line.starts_with('\t') {
            return;
        }
        // Chained-exception connectors are not the cause; the traceback that
        // follows them re-triggers capture and overwrites with the later cause.
        if body.starts_with("During handling of the above exception")
            || body.starts_with("The above exception was the direct cause")
        {
            return;
        }
        // A non-indented, non-connector line is the exception summary
        // (`ExceptionType: message`): record it (bounded, on a char boundary)
        // and stop until another traceback re-triggers capture.
        let mut summary = body.to_string();
        if summary.len() > 300 {
            let mut end = 300;
            while end > 0 && !summary.is_char_boundary(end) {
                end -= 1;
            }
            summary.truncate(end);
        }
        self.cause = Some(summary);
        self.capturing = false;
    }
}

/// The port the desktop app serves on whenever it can have it.
///
/// It is also one of the ports the attach probe below looks at, so a backend
/// left over from a previous run is found and reused instead of becoming a
/// second owner of the same PostgreSQL cluster.
const DEFAULT_BACKEND_PORT: u16 = 8732;

/// Find a port for the backend server, preferring a STABLE one.
///
/// The webview loads the application from `http://127.0.0.1:<port>/`, so the
/// port is the browser origin, and everything the app stores per origin lives
/// and dies with it: the saved session, the chosen interface language, the
/// user's own translation overrides. Picking a fresh random port on every run
/// therefore signed the user out and reset their language on every restart,
/// with nothing on screen to connect the two. Take the default port whenever it
/// is free and only fall back to a picked one when something else holds it.
///
/// Binding and dropping a listener is the only honest way to ask: the bind
/// releases the port at the end of the expression, which leaves the usual tiny
/// race between the check and the sidecar's own bind. That race is what the
/// picker has always had, so this is no weaker than what it replaces.
fn find_available_port() -> u16 {
    if std::net::TcpListener::bind(("127.0.0.1", DEFAULT_BACKEND_PORT)).is_ok() {
        return DEFAULT_BACKEND_PORT;
    }
    portpicker::pick_unused_port().unwrap_or(DEFAULT_BACKEND_PORT)
}

/// Resolve the bundled read-only converters directory shipped as an app
/// resource, if present.
///
/// The Windows installer ships the small (~30 MB) DDC IFC converter under
/// `resources/converters/ifc_windows/` so a fresh install can convert .ifc
/// offline with zero first-use download. We resolve the Tauri resource dir and
/// return the `converters` subfolder only when it actually exists on disk.
/// Returns `None` on platforms or builds that did not ship the converter (every
/// non-Windows build, and any Windows build where the workflow download step was
/// skipped), so the backend silently falls back to its normal install path.
fn bundled_converters_dir(app: &tauri::App) -> Option<PathBuf> {
    let resource_dir = app.path().resource_dir().ok()?;
    let converters = resource_dir.join("converters");
    if converters.is_dir() {
        Some(converters)
    } else {
        None
    }
}

/// Record the resolved local URL so the tray menu and the "open in your
/// browser" command can hand the user the same address the window is showing.
///
/// Also tells the webview the URL (via `setAppUrl`) so the splash first-run
/// card and any in-app control can offer the browser option without having to
/// re-derive the dynamic port.
fn set_app_url(handle: &tauri::AppHandle, url: &str) {
    {
        let state = handle.state::<AppState>();
        *state.app_url.lock().unwrap() = Some(url.to_string());
    }
    let url_js = js_escape(url);
    eval_in_splash(
        handle,
        format!("(function(){{if(typeof setAppUrl==='function'){{setAppUrl('{url_js}');}}}})()"),
    );
}

/// Bring the main app window to the front (used by the tray).
fn show_main_window(handle: &tauri::AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// Ports an already-running OpenConstructionERP backend is likely to be on.
///
/// Checked in order before we spawn our own sidecar. This is what lets the
/// desktop app coexist with a developer backend or a CLI ``openconstructionerp
/// serve`` already running on the same machine: rather than booting a SECOND
/// backend that fights the first one over the shared embedded-PostgreSQL cluster
/// at ``~/.openestimate/pgdata`` (a real founder-machine failure mode), we
/// simply attach to the healthy instance that is already there.
const ATTACH_CANDIDATE_PORTS: [u16; 4] = [8000, 8080, 8732, 8765];

/// The faults that make a backend unusable, asked once for both callers.
///
/// Two decisions in this launcher rest on one health body, and they used to
/// answer it differently. `judge_health` let a backend reporting stale
/// migrations open the application, which is right: the schema being behind is
/// a real problem the user can see and act on from inside the app.
/// `is_our_backend_healthy` refused to attach to that same backend, which sent
/// the launcher off to start a SECOND backend against the same
/// `~/.openestimate/pgdata` - the precise accident the attach path exists to
/// avoid. One running backend was simultaneously fit to be used and unfit to be
/// used, on one field, because the two judgements were written apart.
///
/// So there is one question now, and both ask it. `None` means nothing here
/// stops a user working. `Some(reason)` names a fault that leaves nothing
/// working at all, in words fit to show someone: no database, or an
/// installation with no application files, which answers every route in the app
/// with a 404.
///
/// Everything else stays open on purpose, including a stale migration head and
/// a failed schema heal. This decides whether anyone may use the application at
/// all, so a missing field, a renamed field or a status word we do not know all
/// mean no fault. Only something the backend positively reports may hold a user
/// out of their own installation.
///
/// Takes the parsed body, not the text. What an unreadable body means differs
/// between the two callers and must keep differing: for one it is a stranger to
/// be refused, for the other it is the user's own backend to be trusted. That
/// asymmetry is deliberate, lives at each call site, and is not a bug to tidy.
fn blocking_fault(json: &serde_json::Value) -> Option<String> {
    let status = json.get("status").and_then(|v| v.as_str()).unwrap_or("");
    if status != "degraded" {
        return None;
    }

    let database_down = json
        .get("database")
        .and_then(|v| v.as_str())
        .map(|s| s != "ok")
        .unwrap_or(false);
    if database_down {
        return Some("the local database is not answering".to_string());
    }

    let frontend_missing = json
        .get("frontend_dist_present")
        .and_then(|v| v.as_bool())
        .map(|present| !present)
        .unwrap_or(false);
    if frontend_missing {
        return Some("this installation is missing the application files it serves".to_string());
    }

    None
}

/// Probe ``127.0.0.1:<port>/api/health`` and decide whether we may attach to it.
///
/// Returns ``true`` only when the responder is a backend of EXACTLY OUR version
/// with no fault that would stop us using it. Attaching to anything less is
/// dangerous: a stale dev backend of a different version (the founder-machine
/// case is a degraded v6.10.0 on :8000) would serve the desktop app the wrong
/// frontend and schema. So all of the following must hold, and every rejected
/// candidate is logged with its port, version and status so attach decisions
/// are auditable from the launcher log:
///   * HTTP 2xx, and a body that parses as JSON
///   * ``version`` equals our own ``CARGO_PKG_VERSION`` exactly
///   * ``blocking_fault`` names nothing
///
/// What is deliberately NOT checked here any more is bare ``status ==
/// "degraded"`` and ``alembic_head_matches``. Both rejected a backend that was
/// serving its users perfectly well, and the cost of rejecting was not "we look
/// elsewhere", it was "we start a second backend on the running one's data
/// directory". The founder-machine case that motivated the status check is
/// still rejected, on the check that actually described it: a v6.10.0 responder
/// is not our version. Version equality is what made the status test redundant.
///
/// One of those two has since been fixed at the source, and this note is here so
/// nobody reads the paragraph above as a permanent statement about the backend.
/// A head that trails the tree no longer degrades anything: the backend
/// publishes ``alembic_head_matches`` as a fact and degrades on the condition
/// that actually is a fault, a live schema that has drifted from the models,
/// which it reports separately as ``schema_matches_models``. So the shape that
/// made dropping the head test necessary is not one a current backend produces.
///
/// Neither test goes back in regardless. The launcher attaches to whatever is
/// already listening, and that can be an older build than the launcher itself,
/// so the old shape stays reachable for as long as any such install survives.
/// More to the point, the reason for dropping them was never only that the
/// backend was wrong: rejecting a healthy backend costs a second server on the
/// running one's data directory, and that price is paid whether the field that
/// triggered it was right or not. The fix upstream removes the occasion, not
/// the argument.
async fn is_our_backend_healthy(client: &reqwest::Client, port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/health");
    let resp = match client
        .get(&url)
        .timeout(std::time::Duration::from_millis(1500))
        .send()
        .await
    {
        Ok(resp) => resp,
        // No listener / connection refused / timeout: nothing to log, this is
        // the normal "port is free" case.
        Err(_) => return false,
    };

    let http_status = resp.status();
    if !http_status.is_success() {
        log_line(&format!(
            "attach: rejected candidate on port {port}: HTTP {}",
            http_status.as_u16()
        ));
        return false;
    }

    let body = match resp.text().await {
        Ok(body) => body,
        Err(e) => {
            log_line(&format!(
                "attach: rejected candidate on port {port}: could not read health body ({e})"
            ));
            return false;
        }
    };

    // Parse the health JSON properly so the decision is on real fields, not
    // substring guesses. serde_json is already a dependency.
    let json: serde_json::Value = match serde_json::from_str(&body) {
        Ok(v) => v,
        Err(_) => {
            log_line(&format!(
                "attach: rejected candidate on port {port}: health body is not JSON"
            ));
            return false;
        }
    };

    let status = json.get("status").and_then(|v| v.as_str()).unwrap_or("");
    let version = json.get("version").and_then(|v| v.as_str()).unwrap_or("");
    let our_version = env!("CARGO_PKG_VERSION");

    let version_ok = version == our_version;
    let fault = blocking_fault(&json);

    if version_ok && fault.is_none() {
        log_line(&format!(
            "attach: accepted candidate on port {port}: status={status} version={version}"
        ));
        return true;
    }

    // Name the reason we actually consulted, and name both when both applied. A
    // log that reports a field the decision no longer reads is the same kind of
    // dishonesty as a health flag that reports false when it means unknown.
    let reason = match (version_ok, fault) {
        (false, Some(fault)) => format!("version mismatch, and {fault}"),
        (false, None) => "version mismatch".to_string(),
        (true, Some(fault)) => fault,
        // Not reachable today: this arm is the accepted case, which returned
        // above. It is written out rather than left as a panic because the only
        // thing downstream of it is a log line, and a launcher that aborts while
        // deciding which port to attach to is a worse outcome than a vague one.
        (true, None) => "no fault found".to_string(),
    };
    log_line(&format!(
        "attach: rejected candidate on port {port}: status={status:?} version={version:?} \
(ours={our_version}) reason={reason}"
    ));
    false
}

/// Scan the candidate ports for an existing healthy backend to attach to.
///
/// Returns the first port that responds as our backend, or ``None`` if none do
/// (the normal cold-start case, where we then spawn our own sidecar).
async fn find_existing_backend(client: &reqwest::Client) -> Option<u16> {
    for port in ATTACH_CANDIDATE_PORTS {
        if is_our_backend_healthy(client, port).await {
            return Some(port);
        }
    }
    None
}

/// Where the application server this window will use is going to come from.
///
/// Startup used to answer that question without ever asking it. The loopback
/// probe and the sidecar spawn were one straight line inside `setup`: probe,
/// and if nothing answered, fall through and start a server. Deciding and
/// starting were the same code, so there was no point at which a different
/// answer could have been given, and no name for the thing being decided.
///
/// This type is that point. `resolve_backend_source` decides and returns; the
/// caller carries the decision out. A source that is neither of these is a
/// third variant and a third arm at the call site, and nothing else in `setup`
/// changes shape to admit it.
///
/// Every variant carries the base URL the webview will be sent to, so the
/// address is built once, where the decision is made, instead of being
/// re-derived by whoever acts on it. `port` rides alongside because a loopback
/// server is addressed by port everywhere else in this file: `wait_for_backend`,
/// `watch_backend_liveness` and the clean-shutdown request are all port-typed.
/// A source that is not on loopback would carry no port and would not use them.
enum BackendSource {
    /// A server is already running and needs nothing started; use it as it is.
    AlreadyRunning { base_url: String, port: u16 },
    /// Nothing suitable is running, so this launcher starts one itself.
    StartLocally { base_url: String, port: u16 },
}

/// Decide where this launcher's application server comes from. Starts nothing.
///
/// `local_port` is the port a locally started server would bind, chosen before
/// the Tauri builder ran so that the choice is logged on every run whether or
/// not it ends up being used.
///
/// Attach to an existing healthy backend instead of booting a second one.
///
/// If a developer backend or a CLI `openconstructionerp serve` is already
/// running on this machine, it already owns the embedded PostgreSQL cluster at
/// ~/.openestimate/pgdata. Spawning our own sidecar against the SAME default
/// data dir makes two processes share one cluster; when the desktop app later
/// exits it tells pixeltable-pgserver to clean up, which can stop the postmaster
/// out from under the still-running developer backend. Attaching instead is both
/// safer and faster (no second boot, no second cluster handle). We only attach
/// to a server that self-identifies as ours.
///
/// The probe is short (four ports, 1.5s each at worst, only while they are
/// actually open) and is run to completion here so the decision is made BEFORE
/// anything is started -- otherwise a concurrent probe would race the spawn and
/// we could end up with two backends anyway. block_on is safe: `setup()` already
/// runs on the Tauri async runtime's worker, and the probe never blocks
/// indefinitely.
fn resolve_backend_source(local_port: u16) -> BackendSource {
    let attached_port = tauri::async_runtime::block_on(async {
        let client = reqwest::Client::new();
        find_existing_backend(&client).await
    });

    match attached_port {
        Some(existing) => {
            log_line(&format!(
                "found an existing OpenConstructionERP backend on port {existing}; attaching instead of starting a second one"
            ));
            BackendSource::AlreadyRunning {
                base_url: format!("http://127.0.0.1:{existing}/"),
                port: existing,
            }
        }
        None => {
            log_line("no existing backend found; starting our own sidecar");
            BackendSource::StartLocally {
                base_url: format!("http://127.0.0.1:{local_port}/"),
                port: local_port,
            }
        }
    }
}

/// What one health answer said about the backend.
enum HealthProbe {
    /// No usable answer: nothing listening yet, no reply in time, or a non-2xx.
    Unreachable,
    /// Fit to open the application against.
    Ready,
    /// Answered, and named a fault that makes the application unusable.
    Broken(String),
}

/// The outcome of waiting for the backend to become ready.
enum StartupOutcome {
    Ready,
    /// The backend is up and says it cannot do its job; carries the reason.
    Broken(String),
    /// The wait gave up; carries which of the two limits ran out.
    TimedOut(TimeoutKind),
}

/// Why the startup wait gave up.
enum TimeoutKind {
    /// The backend went quiet: nothing on stdout or stderr for a long time,
    /// which means the step it was on is not progressing.
    WentQuiet(Duration),
    /// The backend kept talking and still never became ready, so the absolute
    /// ceiling ran out.
    TookTooLong,
}

/// What the sidecar's output pump knows about the backend's progress.
///
/// Two different facts, deliberately kept apart:
///
/// * `last_output` moves on ANY line the sidecar writes. It answers "is this
///   backend still doing something", which is the question a timeout should
///   actually ask. Reading only STAGE markers would not answer it - migrations,
///   the module load and first-run seeding emit no markers at all, and a
///   recovering database emits one and then works in silence.
/// * `last_stage` remembers WHICH step the backend last named, so that when the
///   wait does give up it can say what the backend was busy with instead of
///   only that it was slow.
#[derive(Clone)]
struct BootProgress {
    last_output: Arc<Mutex<Instant>>,
    last_stage: Arc<Mutex<Option<(String, String)>>>,
}

impl BootProgress {
    fn new() -> Self {
        Self {
            last_output: Arc::new(Mutex::new(Instant::now())),
            last_stage: Arc::new(Mutex::new(None)),
        }
    }

    /// Record that the sidecar wrote something, whatever it was.
    fn saw_output(&self) {
        if let Ok(mut slot) = self.last_output.lock() {
            *slot = Instant::now();
        }
    }

    /// Record the boot step the sidecar just named, with its detail text.
    fn saw_stage(&self, id: &str, detail: &str) {
        if let Ok(mut slot) = self.last_stage.lock() {
            *slot = Some((id.to_string(), detail.to_string()));
        }
    }

    /// How long the sidecar has said nothing at all.
    ///
    /// A poisoned lock reports zero rather than a huge silence: the timeout
    /// this feeds must never fire because a mutex broke.
    fn quiet_for(&self) -> Duration {
        self.last_output
            .lock()
            .map(|slot| slot.elapsed())
            .unwrap_or_else(|_| Duration::from_secs(0))
    }

    /// The last step the sidecar named, if it named one.
    fn stage(&self) -> Option<(String, String)> {
        self.last_stage.lock().ok().and_then(|slot| slot.clone())
    }
}

/// How long one health probe may take before it counts as no answer.
///
/// Without a per-request bound a backend that accepts the connection and then
/// never answers holds the poll open forever. The deadline below is only tested
/// between polls, so that single hung request outlived the entire startup
/// window: the user waited on the spinner with no timeout message ever shown,
/// because the code that would have shown it never got another turn.
///
/// Generous on purpose. The bound exists to stop one request holding the loop,
/// not to judge how quick the backend is, and this endpoint does real work: a
/// database round trip, a walk of the whole Alembic version tree and a process
/// memory reading. A tight bound would turn a slow first answer on a cold disk
/// into a startup timeout, which is the same lie in the other direction. At
/// twelve seconds there are still dozens of polls inside the startup window.
const HEALTH_PROBE_TIMEOUT: Duration = Duration::from_secs(12);

/// How long the backend may keep reporting a fault that makes it unusable
/// before we stop waiting for it to sort itself out and say so. Startup states
/// clear in seconds; a fault still standing after this is the real state.
const DEGRADED_GRACE: Duration = Duration::from_secs(30);

/// Judge one health body, and fail OPEN.
///
/// The health endpoint answers 200 whether it is healthy or degraded, and the
/// old check read only the status code. So a backend that had answered
/// "degraded, database: error" was treated as ready, the webview was pointed at
/// it, and the user got the application shell with every request inside it
/// failing and nothing to say why. The two faults tested here are the ones that
/// leave nothing working: no database, and an installation with no application
/// files to serve, which answers every route in the app with a 404.
///
/// Everything else stays open on purpose. This function decides whether anyone
/// can start the app at all, so an unreadable body, a missing field, a renamed
/// field or a status word we do not know all mean ready. Only a fault the
/// backend positively reports may hold a user out of their own installation,
/// and stale migrations do not qualify: that is a real problem, and it is one
/// the user can still see and act on from inside the app.
///
/// The fault test itself is `blocking_fault`, shared with the attach probe so
/// the two cannot drift apart again. Only the unreadable-body case is decided
/// here, and it stays decided here: this is the user's own backend, so a body
/// we cannot parse means open the app. The attach probe reaches the opposite
/// conclusion on the same input, because there the responder is a stranger.
fn judge_health(body: &str) -> HealthProbe {
    let json: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(_) => return HealthProbe::Ready,
    };

    match blocking_fault(&json) {
        Some(reason) => HealthProbe::Broken(reason),
        None => HealthProbe::Ready,
    }
}

/// How long the backend may say nothing at all before the wait gives up.
///
/// The wait used to decide on elapsed time alone, so a backend that had
/// reported progress one second ago was still abandoned the moment the window
/// closed - and the window has to be long enough for the slowest legitimate
/// start, which is why it was twenty minutes. Silence is the better signal: a
/// sidecar that is working writes to its log, and one that is wedged does not.
///
/// Four minutes, and not less, because some legitimate steps are quiet for a
/// while: a single long migration, or first-run demo seeding on a slow disk.
/// Crash recovery, the longest quiet step there was, now reports itself every
/// fifteen seconds (`_RECOVERY_HEARTBEAT_SECONDS` in `app/core/embedded_pg.py`),
/// so the backend that this limit abandons is one that really has stopped.
/// Abandoning a working backend is strictly worse than waiting longer for a
/// broken one, so when in doubt this number goes up, not down.
const STARTUP_QUIET_TIMEOUT: Duration = Duration::from_secs(240);

/// Wait for the backend to become fit to open.
///
/// Polls `/api/health` every ~500ms until it is ready, reports a standing fault,
/// runs out of patience with a backend that has gone quiet, or reaches the
/// absolute ceiling. While waiting, updates the splash screen so the user sees
/// progress; first-run embedded-PostgreSQL setup can be slow.
async fn wait_for_backend(
    handle: &tauri::AppHandle,
    port: u16,
    timeout_secs: u64,
    progress: &BootProgress,
) -> StartupOutcome {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{port}/api/health");
    let start = Instant::now();
    let mut progress_shown = false;
    let mut broken_since: Option<Instant> = None;
    let mut broken_logged = false;

    while start.elapsed().as_secs() < timeout_secs {
        // Checked before the probe, so a backend that has gone quiet is given
        // up on at the quiet limit rather than one poll later.
        let quiet_for = progress.quiet_for();
        if quiet_for >= STARTUP_QUIET_TIMEOUT {
            return StartupOutcome::TimedOut(TimeoutKind::WentQuiet(quiet_for));
        }

        let probe = match client.get(&url).timeout(HEALTH_PROBE_TIMEOUT).send().await {
            Ok(resp) if resp.status().is_success() => match resp.text().await {
                Ok(body) => judge_health(&body),
                // The status line arrived and the body did not. Something is
                // serving; do not hold the user out over a lost read.
                Err(_) => HealthProbe::Ready,
            },
            _ => HealthProbe::Unreachable,
        };

        match probe {
            HealthProbe::Ready => return StartupOutcome::Ready,
            HealthProbe::Broken(reason) => {
                if !broken_logged {
                    broken_logged = true;
                    log_line(&format!("backend answered but reports a fatal fault: {reason}"));
                }
                let since = *broken_since.get_or_insert_with(Instant::now);
                if since.elapsed() >= DEGRADED_GRACE {
                    return StartupOutcome::Broken(reason);
                }
            }
            HealthProbe::Unreachable => {
                // A backend that has stopped answering is starting or dying,
                // not degraded, so an earlier degraded reading must not be held
                // against the next one.
                broken_since = None;
                broken_logged = false;
            }
        }

        if !progress_shown && start.elapsed().as_secs() >= 8 {
            progress_shown = true;
            if let Some(window) = handle.get_webview_window("main") {
                let _ = window.eval("setStatus('Setting up the local database, almost there')");
            }
        }

        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    }
    StartupOutcome::TimedOut(TimeoutKind::TookTooLong)
}

/// A fresh secret for the backend's shutdown endpoint, one per run of the app.
///
/// Two v4 UUIDs, because one is 122 bits of randomness and two are cheap. The
/// value only ever travels between this process and the backend it starts, so
/// there is nothing to rotate and nothing to store: a new run gets a new token,
/// and the backend from the previous run - if one somehow outlived us - will
/// not accept it, which is the correct answer, because that backend is not ours
/// to stop.
fn new_shutdown_token() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    )
}

/// The message shown when the backend never became ready.
///
/// Names the step it was on. "The application backend did not start in time"
/// told a user only that they had waited, which is the one thing they already
/// knew; the launcher has always known which step the backend last reported and
/// simply did not say it.
fn startup_timeout_message(stage: Option<&(String, String)>, kind: &TimeoutKind) -> String {
    let tail = "Please close this window and try again. If the problem persists, please send the \
log file to info@datadrivenconstruction.io.";

    let Some((id, detail)) = stage else {
        // Nothing was ever reported, so there is no step to name and the old
        // wording is still the honest one.
        return format!("The application backend did not start in time. {tail}");
    };

    let step = describe_stage(id);
    let note = if detail.is_empty() {
        String::new()
    } else {
        format!(" The last thing it reported was: {detail}.")
    };

    match kind {
        TimeoutKind::WentQuiet(quiet_for) => format!(
            "The application backend stopped responding while {step}. It has reported nothing \
for {} minutes.{note} {tail}",
            quiet_for.as_secs() / 60
        ),
        TimeoutKind::TookTooLong => format!(
            "The application backend is still {step} and did not finish in time.{note} {tail}"
        ),
    }
}

/// Turn a boot-stage id into something a person can read.
///
/// Only ids the sidecar itself reports can reach this, so the launcher's own
/// checklist ids are not listed: an id with no words of its own would be
/// indistinguishable from the fallback, which is the very failure this exists
/// to fix.
fn describe_stage(id: &str) -> &'static str {
    match id {
        "sidecar" => "starting its backend component",
        "pg" => "preparing the local database",
        "migrate" => "updating the local database",
        "model" => "installing the semantic search model",
        "server" => "starting the application server",
        "open" => "opening the application",
        _ => "starting up",
    }
}

/// Use a server that is already running: mark the checklist complete and open
/// the app against it.
///
/// Nothing is started here. This is one of the two things `setup` can do with
/// a `BackendSource`, and it is the arm that does the least: the server exists,
/// so all that is left is to say so and navigate to it.
fn attach_to_running_backend(
    handle: tauri::AppHandle,
    base_url: String,
    port: u16,
    shutting_down: Arc<AtomicBool>,
    backend_lost: Arc<AtomicBool>,
) {
    boot_stage(&handle, "sidecar", "done", "Found a running backend");
    boot_stage(&handle, "pg", "done", "");
    boot_stage(&handle, "migrate", "done", "");
    boot_stage(&handle, "server", "done", "");
    boot_stage(&handle, "open", "done", "Ready");
    let url = base_url;
    set_app_url(&handle, &url);
    let handle_nav = handle.clone();
    tauri::async_runtime::spawn(async move {
        // Give the splash script a moment to finish loading, then
        // let it offer the one-time "app window or browser" choice
        // before navigating the webview to the running app.
        tokio::time::sleep(std::time::Duration::from_millis(400)).await;
        if let Some(window) = handle_nav.get_webview_window("main") {
            let _ = window.show();
            let _ = window.set_focus();
            let url_js = js_escape(&url);
            let _ = window.eval(&format!(
                "(function(){{if(typeof offerLaunchChoice==='function'){{\
                    offerLaunchChoice('{url_js}');}}\
                    else{{window.location.replace('{url_js}');}}}})()"
            ));
        }
        update_check::note_app_started(&handle_nav, env!("CARGO_PKG_VERSION"));
    });
    // We did not spawn a sidecar, so there is no child to manage.
    // That is exactly why the liveness watch matters most here: the
    // backend belongs to another process, nothing reports its exit
    // to us, and without this its death would leave the window
    // showing an application that no longer has a server.
    tauri::async_runtime::spawn(watch_backend_liveness(
        handle.clone(),
        port,
        shutting_down.clone(),
        backend_lost.clone(),
    ));
}

/// Start a server locally, as a sidecar of this process, and open the app
/// against it once it is healthy.
///
/// This is the other arm of `BackendSource`, and it is what every user gets
/// today. It used to be the tail of `setup` with nothing separating it from
/// the decision to run it, which is why there was no place to put a second
/// answer. `base_url` is the address this server will be reachable on, handed
/// in by whoever made the decision rather than rebuilt here.
fn start_local_backend(
    handle: tauri::AppHandle,
    base_url: String,
    port: u16,
    bundled_converters: Option<PathBuf>,
    shutting_down: Arc<AtomicBool>,
    backend_lost: Arc<AtomicBool>,
) {
    // Read the shutdown secret out of managed state here, in
    // synchronous code, so the spawn below can hand it to the child.
    let shutdown_token = handle.state::<AppState>().shutdown_token.clone();

    // Start the backend sidecar.
    //
    // The "serve" subcommand is required: the CLI only accepts --host /
    // --port under a subcommand. Invoked bare it would ignore them,
    // fall back to defaults, and on first run block on an interactive
    // "open in browser?" stdin prompt that a sidecar has no terminal
    // for. With --data-dir left unset the sidecar uses its default
    // (~/.openestimate), which stays writable even for a per-machine
    // install under Program Files.
    let shell = handle.shell();
    let sidecar_cmd = match shell.sidecar("openconstructionerp-server") {
        Ok(cmd) => {
            // OE_DESKTOP=1 marks this backend as one we spawned from the
            // desktop shell (so the backend can run desktop-only
            // bootstrapping). We deliberately do NOT set it on the attach
            // path above, because an already-running dev backend must not
            // be treated as a desktop-bootstrapped one.
            // The second variable is the secret for the backend's own
            // shutdown endpoint, which is how this launcher stops it
            // cleanly on the way out. A backend started without it
            // refuses to shut down on request at all, which is the
            // right answer for every backend we did not start.
            let mut cmd = cmd
                .env("OE_DESKTOP", "1")
                .env("OE_DESKTOP_SHUTDOWN_TOKEN", shutdown_token.as_str())
                .args([
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    &port.to_string(),
                ]);
            // Point the backend at the bundled read-only converters so
            // an .ifc upload converts offline with no first-use download.
            // Only set when we actually shipped a converters dir; absent
            // env keeps the normal auto-download path intact.
            if let Some(ref dir) = bundled_converters {
                cmd = cmd.env("OE_BUNDLED_CONVERTERS_DIR", dir.as_os_str());
            }
            // Give the sidecar a working directory it is allowed to
            // write to. Eighteen upload roots across ten modules are
            // declared as working-directory-relative literals and
            // create themselves with mkdir on first use, bypassing the
            // data-dir plumbing the rest of the platform goes through.
            // Nothing set the child's working directory, so it inherited
            // this process's, and the Start Menu shortcut of a
            // per-machine install starts the app in the install folder
            // under Program Files. Creating a directory there is denied
            // to an unelevated user, so attaching a file to a request
            // for information, a submittal, an inspection, a punch item,
            // a letter, a diary entry, a lien waiver, a closeout item or
            // a compliance document returned a bare 500 on every such
            // install, with ten registers carrying an attach button that
            // could not work.
            //
            // The note above about --data-dir is correct and was not
            // enough: it keeps the data directory writable and says
            // nothing about the working directory, which is a second
            // path the same process resolves against. Development and CI
            // both run with the repository root as the working
            // directory, which is writable, so those eighteen modules
            // look healthy everywhere they are ever tested.
            //
            // This moves them somewhere writable without editing the
            // eighteen declarations. It is a floor, not the repair: they
            // should answer to OE_CLI_DATA_DIR like everything else, and
            // until they do, a relative path written by any new module
            // lands here by accident rather than by design. Failing
            // through silently is deliberate, because an inherited
            // working directory is exactly what shipped, so a machine
            // whose home directory cannot be read is left no worse off
            // than it is today.
            if let Some(home) = home_dir() {
                let workdir = home.join(".openestimate");
                if std::fs::create_dir_all(&workdir).is_ok() {
                    cmd = cmd.current_dir(&workdir);
                }
            }
            cmd
        }
        Err(e) => {
            report_fatal_stage(
                &handle,
                "sidecar",
                &format!(
                    "Could not locate the backend component ({e}). Please reinstall \
the application."
                ),
            );
            // Keep the window open so the user sees the error.
            return;
        }
    };

    let (mut rx, child) = match sidecar_cmd.spawn() {
        Ok(pair) => pair,
        Err(e) => {
            report_fatal_stage(
                &handle,
                "sidecar",
                &format!(
                    "The backend component could not be started ({e}). Some antivirus \
tools block newly installed programs; allow OpenConstructionERP and try again."
                ),
            );
            return;
        }
    };
    log_line("sidecar spawned");
    boot_stage(&handle, "sidecar", "done", "");
    boot_stage(&handle, "pg", "active", "Starting the local database");

    // Keep the child handle alive (and stoppable on exit). The port
    // goes in beside it, because the clean stop is a request to the
    // backend and a request needs an address; it is recorded HERE, on
    // the spawn path only, so the exit path can never ask a backend we
    // merely attached to to shut itself down.
    let backend_exited = {
        let state = handle.state::<AppState>();
        *state.backend_child.lock().unwrap() = Some(child);
        *state.backend_port.lock().unwrap() = Some(port);
        state.backend_exited.clone()
    };

    let backend_ready = Arc::new(AtomicBool::new(false));
    // Separate from readiness on purpose. Readiness means the backend
    // answered a health check, so it is only ever set on success and
    // says nothing about whether a failure has already been explained.
    // This one means the user has been shown a precise reason, which is
    // the question the startup timeout below actually needs answered.
    let fatal_reported = Arc::new(AtomicBool::new(false));
    let last_stderr = Arc::new(Mutex::new(String::new()));
    // Latch the real startup failure cause so the database-shutdown
    // noise that follows a crash cannot mask it: a STAGE:server:fail
    // marker (preferred), or the exception line of a Python traceback
    // on stderr when the backend died too early to emit a marker.
    let latched_fail = Arc::new(Mutex::new(None::<String>));
    let traceback = Arc::new(Mutex::new(TracebackCapture::default()));
    // Latched the same way and in the same pump as the two above,
    // because the startup wait needs to know whether the backend is
    // still working and what it is working on, and the pump is the only
    // place that sees either.
    let boot_progress = BootProgress::new();

    // Pump the sidecar's output into the log file and remember recent
    // stderr so a startup crash can be shown to the user verbatim.
    {
        let ready = backend_ready.clone();
        let fatal_flag = fatal_reported.clone();
        let stderr_buf = last_stderr.clone();
        let latched = latched_fail.clone();
        let traceback = traceback.clone();
        let handle_evt = handle.clone();
        let exited_flag = backend_exited.clone();
        let deliberate = shutting_down.clone();
        let lost_flag = backend_lost.clone();
        let progress = boot_progress.clone();
        tauri::async_runtime::spawn(async move {
            while let Some(event) = rx.recv().await {
                match event {
                    CommandEvent::Stdout(bytes) => {
                        let line = String::from_utf8_lossy(&bytes);
                        log_line(&format!("[backend] {}", line.trim_end()));
                        // A line, any line, is the backend saying it is
                        // still working.
                        progress.saw_output();
                        // Drive the visible boot checklist from the
                        // backend's machine-readable progress markers.
                        for raw in line.split('\n') {
                            if let Some((id, status, detail)) = parse_stage_marker(raw) {
                                boot_stage(&handle_evt, &id, &status, &detail);
                                progress.saw_stage(&id, &detail);
                                // Latch the first real failure cause.
                                if status == "failed" && !detail.is_empty() {
                                    let mut lf = latched.lock().unwrap();
                                    if lf.is_none() {
                                        *lf = Some(detail.clone());
                                    }
                                }
                            }
                        }
                    }
                    CommandEvent::Stderr(bytes) => {
                        let line = String::from_utf8_lossy(&bytes);
                        log_line(&format!("[backend:err] {}", line.trim_end()));
                        // Counts as working too: most of what a healthy
                        // start writes - uvicorn's own log, alembic,
                        // the module loader - comes out on stderr.
                        progress.saw_output();
                        // Some launchers/loggers route progress markers
                        // to stderr; honour them there too. Non-marker
                        // lines feed the traceback capture so a hard
                        // crash still yields a real cause with no marker.
                        for raw in line.split('\n') {
                            if let Some((id, status, detail)) = parse_stage_marker(raw) {
                                boot_stage(&handle_evt, &id, &status, &detail);
                                progress.saw_stage(&id, &detail);
                                if status == "failed" && !detail.is_empty() {
                                    let mut lf = latched.lock().unwrap();
                                    if lf.is_none() {
                                        *lf = Some(detail.clone());
                                    }
                                }
                            } else {
                                traceback.lock().unwrap().feed_line(raw);
                            }
                        }
                        let mut buf = stderr_buf.lock().unwrap();
                        buf.push_str(&line);
                        if buf.len() > 4000 {
                            // Advance the cut to a char boundary so
                            // slicing a UTF-8 string can never panic and
                            // kill the pump (which would strand the user
                            // on the splash for the whole timeout).
                            let mut cut = buf.len() - 4000;
                            while cut < buf.len() && !buf.is_char_boundary(cut) {
                                cut += 1;
                            }
                            *buf = buf[cut..].to_string();
                        }
                    }
                    CommandEvent::Error(err) => {
                        log_line(&format!("[backend:error] {err}"));
                        // A failure reading the child's own pipes went
                        // to the log and nowhere else, so on a startup
                        // that then failed the user was shown a tail of
                        // stderr with no sign of it. Add it to that tail
                        // rather than latching it as the cause: it is
                        // usually a symptom of the crash, and it must
                        // not displace the exception that explains it.
                        let mut buf = stderr_buf.lock().unwrap();
                        buf.push_str(&format!("launcher: {err}\n"));
                    }
                    CommandEvent::Terminated(payload) => {
                        log_line(&format!(
                            "[backend] terminated: code={:?} signal={:?}",
                            payload.code, payload.signal
                        ));
                        // Record the exit before anything else, so the
                        // shutdown path can wait for the process to
                        // really be gone instead of assuming it.
                        exited_flag.store(true, Ordering::SeqCst);
                        // If the backend died before ever becoming
                        // healthy, surface it now instead of leaving the
                        // user staring at the spinner for the full timeout.
                        if !ready.load(Ordering::SeqCst) {
                            // Prefer the real cause the backend reported
                            // (STAGE:server:fail), then the exception line
                            // of a captured Python traceback, and only as a
                            // last resort the raw stderr tail. This is what
                            // keeps the database-shutdown noise from masking
                            // the real reason startup failed.
                            let latched_cause = latched.lock().unwrap().clone();
                            let tb_cause = traceback.lock().unwrap().cause.clone();
                            let core = if let Some(cause) = latched_cause.or(tb_cause) {
                                format!("The backend could not finish starting: {cause}")
                            } else {
                                let tail = stderr_buf.lock().unwrap().clone();
                                if tail.trim().is_empty() {
                                    format!(
                                        "The backend stopped unexpectedly (exit code {:?}) \
before it finished starting.",
                                        payload.code
                                    )
                                } else {
                                    // Last resort: show the tail of stderr,
                                    // which usually carries the cause.
                                    let trimmed = tail.trim();
                                    let shown = if trimmed.len() > 600 {
                                        let mut start = trimmed.len() - 600;
                                        while start < trimmed.len()
                                            && !trimmed.is_char_boundary(start)
                                        {
                                            start += 1;
                                        }
                                        &trimmed[start..]
                                    } else {
                                        trimmed
                                    };
                                    format!(
                                        "The backend stopped unexpectedly during startup: {shown}"
                                    )
                                }
                            };
                            // Always pair the cause with a clear next step.
                            // report_fatal_stage also surfaces the log path
                            // (the splash shows an Open-log button).
                            let detail = format!(
                                "{core} Open the log file for the full details, and if \
this keeps happening send it to info@datadrivenconstruction.io."
                            );
                            // Attribute the failure to the server step so
                            // the checklist shows a clear red mark.
                            report_fatal_stage(&handle_evt, "server", &detail);
                            // Record that the user now has the real
                            // reason, so the startup timeout does not
                            // replace it with a vaguer one later.
                            fatal_flag.store(true, Ordering::SeqCst);
                        } else if !deliberate.load(Ordering::SeqCst) {
                            // The backend had already gone healthy, and
                            // nobody asked it to stop. This case was
                            // silent: readiness was the end of the
                            // launcher's attention, so a sidecar that
                            // died an hour in left the window showing
                            // the last screen it had rendered while
                            // every request inside it failed, and the
                            // only record was a line in a log file the
                            // user had no reason to open.
                            report_backend_lost(
                                &handle_evt,
                                &lost_flag,
                                "The application backend has stopped",
                                &format!(
                                    "The backend exited unexpectedly (exit code {:?}), so \
this window can no longer load or save anything. Please close it and start \
OpenConstructionERP again. If this keeps happening, send the log file to \
info@datadrivenconstruction.io.",
                                    payload.code
                                ),
                            );
                        }
                        break;
                    }
                    _ => {}
                }
            }

            // The event channel can also just end: the sender is
            // dropped and no Terminated event ever arrives. Reaching
            // here means we have stopped watching the backend, and
            // saying nothing would leave whoever is using it to find
            // out from a screen that no longer works.
            if !exited_flag.load(Ordering::SeqCst)
                && !deliberate.load(Ordering::SeqCst)
                && ready.load(Ordering::SeqCst)
            {
                log_line("backend event stream ended without a termination event");
                report_backend_lost(
                    &handle_evt,
                    &lost_flag,
                    "The connection to the application backend was lost",
                    "The launcher can no longer see the backend it started, so this \
window may stop working. Please close it and start OpenConstructionERP again. If this keeps \
happening, send the log file to info@datadrivenconstruction.io.",
                );
            }
        });
    }

    // Wait for the backend to be ready, then navigate the webview from
    // the splash screen to the live application. First-run embedded
    // PostgreSQL setup (initdb, migrations, module load, demo seed) can
    // be slow on a cold machine, so allow up to 240 seconds.
    let handle_clone = handle.clone();
    let ready_flag = backend_ready.clone();
    let fatal_flag_wait = fatal_reported.clone();
    let shutting_down_wait = shutting_down.clone();
    let backend_lost_wait = backend_lost.clone();
    let progress_wait = boot_progress.clone();
    let base_url_wait = base_url;
    tauri::async_runtime::spawn(async move {
        // A first run that has to recover a large local database (WAL
        // replay + fsync) can take several minutes, so allow a generous
        // window. This number has one hard requirement: it must exceed
        // the backend's own budget for bringing embedded PostgreSQL up,
        // which is OE_PG_BOOT_TIMEOUT and defaults to 600s. It used to
        // be 600 as well, so the two were equal and a database that
        // spent its whole budget recovering left nothing at all for the
        // work that follows it: migrations, the module load, table
        // creation and first-run seeding. That is not a hypothetical
        // ordering. The installer stops a running instance by killing
        // the process tree, which crash-stops the embedded database, so
        // the next start after every upgrade is exactly the WAL replay
        // the 600s budget exists for. The user then saw a healthy,
        // still-working backend reported as one that had not started,
        // and retrying reproduced it because nothing had gone wrong to
        // clear. Doubling it keeps a comfortable margin above the inner
        // budget and costs nothing when a backend has genuinely failed,
        // because that path reports itself the moment the sidecar dies
        // rather than waiting for this window to close.
        //
        // This is the ceiling and no longer the only limit: a backend
        // that goes quiet is given up on after STARTUP_QUIET_TIMEOUT,
        // so the full window is only ever spent on a backend that is
        // demonstrably still working.
        match wait_for_backend(&handle_clone, port, 1200, &progress_wait).await {
            StartupOutcome::Ready => {
                ready_flag.store(true, Ordering::SeqCst);
                log_line("backend healthy; navigating to app");
                boot_stage(&handle_clone, "server", "done", "");
                boot_stage(&handle_clone, "open", "done", "Ready");
                    let url = base_url_wait;
                set_app_url(&handle_clone, &url);
                if let Some(window) = handle_clone.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                    // Let the splash offer the one-time "app window or
                    // browser" choice; if that helper is missing for any
                    // reason, fall straight through to the app window so
                    // the user is never left on the splash.
                    let url_js = js_escape(&url);
                    let _ = window.eval(&format!(
                        "(function(){{if(typeof offerLaunchChoice==='function'){{\
                            offerLaunchChoice('{url_js}');}}\
                            else{{window.location.replace('{url_js}');}}}})()"
                    ));
                }
                update_check::note_app_started(&handle_clone, env!("CARGO_PKG_VERSION"));
                // Readiness is where the launcher used to stop looking.
                // Keep watching, so a backend that goes away later is
                // reported instead of being left for the user to find.
                tauri::async_runtime::spawn(watch_backend_liveness(
                    handle_clone.clone(),
                    port,
                    shutting_down_wait,
                    backend_lost_wait,
                ));
            }
            StartupOutcome::Broken(reason) => {
                // The backend is answering and telling us it cannot do
                // its job. Opening the app on top of that hands the user
                // a shell whose every action fails, which is how this
                // ended up looking like the product was broken rather
                // than the installation.
                log_line(&format!("backend is up but not fit to serve: {reason}"));
                if !fatal_flag_wait.load(Ordering::SeqCst) {
                    report_fatal_stage(
                        &handle_clone,
                        "server",
                        &format!(
                            "The backend started, but {reason}, so the app cannot open. \
Please close this window and try again. If the problem persists, please send the log file to \
info@datadrivenconstruction.io."
                        ),
                    );
                }
            }
            StartupOutcome::TimedOut(kind) => {
                let stage = progress_wait.stage();
                match &kind {
                    TimeoutKind::WentQuiet(quiet_for) => log_line(&format!(
                        "backend went quiet during startup: nothing written for {}s, last step reported was {}",
                        quiet_for.as_secs(),
                        stage
                            .as_ref()
                            .map(|(id, _)| id.as_str())
                            .unwrap_or("none"),
                    )),
                    TimeoutKind::TookTooLong => log_line(&format!(
                        "backend did not become healthy within the startup window; last step reported was {}",
                        stage
                            .as_ref()
                            .map(|(id, _)| id.as_str())
                            .unwrap_or("none"),
                    )),
                }
                // Only say "slow" when nothing better has been said. The
                // termination handler above names the real cause the
                // moment the sidecar dies, and this branch used to guard
                // on the readiness flag, which is set only when the
                // backend goes healthy. A backend that died during
                // startup therefore left readiness false and satisfied
                // this condition, so the timeout fired minutes afterwards
                // and overwrote a message carrying the actual exception
                // with one that said only that the backend had not
                // started in time. A user whose sidecar exited with a
                // FileNotFoundError nine minutes earlier read the second
                // message, looked for the fault on their own machine, and
                // had no way to know the first had ever been shown.
                if !ready_flag.load(Ordering::SeqCst)
                    && !fatal_flag_wait.load(Ordering::SeqCst)
                {
                    report_fatal_stage(
                        &handle_clone,
                        "server",
                        &startup_timeout_message(stage.as_ref(), &kind),
                    );
                }
            }
        }
    });
}

fn main() {
    // Write the diagnostic log at the VERY FIRST instruction, before anything
    // else in startup can fail. If the user reports "I click the icon and
    // nothing happens", this line guarantees the log file at least exists and
    // records that the process launched -- so the failure is never invisible,
    // even if building the Tauri app itself (WebView2 missing, etc.) blows up
    // before any window appears.
    log_line(&format!(
        "=== OpenConstructionERP desktop launcher starting (v{}) ===",
        env!("CARGO_PKG_VERSION")
    ));

    let port = find_available_port();
    log_line(&format!("selected backend port {port}"));

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(AppState {
            backend_child: Mutex::new(None),
            app_url: Mutex::new(None),
            shutting_down: Arc::new(AtomicBool::new(false)),
            backend_exited: Arc::new(AtomicBool::new(false)),
            backend_lost_reported: Arc::new(AtomicBool::new(false)),
            backend_port: Mutex::new(None),
            shutdown_token: new_shutdown_token(),
        })
        .invoke_handler(tauri::generate_handler![
            open_log_file,
            open_app_in_browser,
            open_external_url,
            get_app_url,
            update_check::set_update_check_enabled,
            update_check::decline_update_version
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            log_line(&format!("setup() running; backend port = {port}"));

            // Take the shared flags out of managed state once, here in
            // synchronous code, and hand the tasks below plain Arcs. A
            // `State` borrow lives on the handle it came from, and the tasks
            // that need these flags are async and long-lived.
            let (shutting_down, backend_lost) = {
                let state = handle.state::<AppState>();
                (
                    state.shutting_down.clone(),
                    state.backend_lost_reported.clone(),
                )
            };

            // Resolve the bundled read-only converters dir (Windows ships the
            // DDC IFC converter as an app resource) BEFORE the attach/spawn
            // branches so we can hand its path to whichever sidecar we start.
            // None on builds that did not ship it, in which case the backend
            // keeps its normal auto-download behaviour.
            let bundled_converters = bundled_converters_dir(app);
            if let Some(ref dir) = bundled_converters {
                log_line(&format!("bundled converters dir: {}", dir.display()));
            }

            // Surface the log path and the first two checklist steps right away
            // so the user sees a live boot screen the instant the window paints.
            report_log_path(&handle);
            boot_stage(&handle, "launcher", "done", "");
            // Show the version from the first frame, not only once something has
            // failed, so a user who is merely puzzled can also read it off.
            report_app_version(&handle);
            boot_stage(&handle, "sidecar", "active", "Locating the backend");

            // Ask, in the background, whether a newer release exists. Started
            // here rather than when a failure is reported because a failure can
            // arrive in milliseconds - a sidecar binary that is not there fails
            // long before any request could finish - and a user staring at an
            // error is not going to be made to wait for a web request on top of
            // it. Never awaited, never blocking, and silent unless startup
            // fails: see the file for what it does and does not do.
            update_check::spawn(handle.clone(), env!("CARGO_PKG_VERSION").to_string());

            // Tray icon with a right-click menu. The menu is the always-present
            // home for the "open in your browser" choice the founder asked for:
            // however the app was started, the user can right-click the tray
            // icon and pick whether to keep using the app window or hand the
            // local address to their normal web browser. Building the tray is a
            // nice-to-have, so its failure must never abort startup.
            let tray_menu_result = (|| {
                let show_item = MenuItemBuilder::with_id("tray_show", "Show app window")
                    .build(app)?;
                let browser_item =
                    MenuItemBuilder::with_id("tray_open_browser", "Open in your browser")
                        .build(app)?;
                let quit_item = MenuItemBuilder::with_id("tray_quit", "Quit").build(app)?;
                let sep = PredefinedMenuItem::separator(app)?;
                MenuBuilder::new(app)
                    .item(&show_item)
                    .item(&browser_item)
                    .item(&sep)
                    .item(&quit_item)
                    .build()
            })();

            let tray_build = match tray_menu_result {
                Ok(menu) => TrayIconBuilder::new()
                    .icon(app.default_window_icon().cloned().unwrap_or_else(|| {
                        // Should never happen (the bundle always ships an icon),
                        // but fall back to a 1x1 transparent pixel rather than
                        // panic, keeping the no-panic startup contract.
                        tauri::image::Image::new_owned(vec![0, 0, 0, 0], 1, 1)
                    }))
                    .tooltip("OpenConstructionERP")
                    .menu(&menu)
                    // Keep the existing left-click-to-show behaviour. The menu
                    // shows on right-click; suppressing menu-on-left-click means
                    // a single left click still just raises the window.
                    .show_menu_on_left_click(false)
                    .on_menu_event(|app, event| match event.id().as_ref() {
                        "tray_show" => show_main_window(app),
                        "tray_open_browser" => {
                            if let Err(e) = open_app_in_browser(app.clone(), None) {
                                log_line(&format!("tray: open in browser failed: {e}"));
                            }
                        }
                        "tray_quit" => app.exit(0),
                        _ => {}
                    })
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        } = event
                        {
                            show_main_window(tray.app_handle());
                        }
                    })
                    .build(app),
                Err(e) => {
                    log_line(&format!("warning: tray menu build failed (non-fatal): {e}"));
                    // Fall back to the plain icon-only tray (left-click shows).
                    TrayIconBuilder::new()
                        .tooltip("OpenConstructionERP")
                        .on_tray_icon_event(|tray, event| {
                            if let TrayIconEvent::Click {
                                button: MouseButton::Left,
                                button_state: MouseButtonState::Up,
                                ..
                            } = event
                            {
                                show_main_window(tray.app_handle());
                            }
                        })
                        .build(app)
                }
            };
            if let Err(e) = tray_build {
                log_line(&format!("warning: tray icon build failed (non-fatal): {e}"));
            }

            // Decide where the application server comes from, then carry that
            // decision out. Choosing and starting are two steps now rather than
            // one, so a server this launcher did not start has a place to be
            // chosen; see `BackendSource`. Both of today's answers are still
            // local, and both behave exactly as they did when this was one
            // straight line of code.
            match resolve_backend_source(port) {
                BackendSource::AlreadyRunning { base_url, port } => {
                    attach_to_running_backend(handle, base_url, port, shutting_down, backend_lost);
                }
                BackendSource::StartLocally { base_url, port } => {
                    start_local_backend(
                        handle,
                        base_url,
                        port,
                        bundled_converters,
                        shutting_down,
                        backend_lost,
                    );
                }
            }

            Ok(())
        })
        .build(tauri::generate_context!());

    match app {
        Ok(app) => app.run(|app_handle, event| match event {
            // Both events, because nothing the sidecar owns may outlive the
            // launcher on any exit path, and stopping it twice is a no-op.
            RunEvent::ExitRequested { .. } | RunEvent::Exit => stop_backend(app_handle),
            _ => {}
        }),
        Err(e) => {
            // Building the Tauri app itself failed. There is no Tauri window to
            // show an error in, so at least leave a breadcrumb in the log...
            let message = format!("FATAL: error building Tauri application: {e}");
            log_line(&message);
            // ...and, on Windows, pop a NATIVE message box so the failure is
            // never silent again (the v7.0.0 updater-plugin crash exited within
            // 2s with nothing on screen). MessageBoxW is a bare Win32 call via
            // windows-sys, so it works even though no Tauri/WebView2 window
            // exists.
            show_startup_failure_dialog(&message);
        }
    }
}

/// How long the launcher waits for the backend to actually be gone.
///
/// A window that is already closed while its process lingers reads as a hang,
/// and at session logoff Windows gives an application very little time before it
/// is killed anyway, so this is a short wait for confirmation and not a budget
/// for the backend to finish work in.
const BACKEND_STOP_WAIT: Duration = Duration::from_secs(5);

/// How long the backend is given to stop itself once it accepts the request to.
///
/// Longer than the wait above, because this one is not a formality: the backend
/// is disposing its database engine and stopping the PostgreSQL cluster, and a
/// cluster with a large checkpoint to write takes a few seconds over it. Those
/// seconds are the entire point. Every one of them not spent here comes back on
/// the next start as write-ahead-log replay, which is measured in minutes.
const GRACEFUL_STOP_WAIT: Duration = Duration::from_secs(10);

/// How long the shutdown request itself may take to be answered.
///
/// The backend answers before it acts, so this bounds a round trip on loopback
/// and not the shutdown. A backend too busy to answer within it is one we go on
/// to stop the hard way.
const GRACEFUL_REQUEST_TIMEOUT: Duration = Duration::from_secs(3);

/// Step one, on every platform: ask the backend to shut itself down.
///
/// This is the only stop that is clean on Windows. A forced stop leaves the
/// embedded PostgreSQL cluster looking crashed, so the next start replays its
/// write-ahead log, which on a large cluster takes minutes - and that wait is
/// what users have been reading as "the application backend did not start in
/// time", on a machine where nothing was wrong.
///
/// Returns whether the backend accepted the request. It refuses unless it is a
/// desktop-mode backend, reached over loopback, presented with the token this
/// launcher generated for it; see `backend/app/core/desktop_shutdown.py`.
fn ask_backend_to_stop(port: u16, token: &str) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/system/desktop-shutdown");
    tauri::async_runtime::block_on(async {
        let client = match reqwest::Client::builder()
            .timeout(GRACEFUL_REQUEST_TIMEOUT)
            .build()
        {
            Ok(client) => client,
            Err(e) => {
                log_line(&format!(
                    "backend stop: could not build the shutdown client: {e}"
                ));
                return false;
            }
        };

        match client
            .post(&url)
            .header("X-Desktop-Shutdown-Token", token)
            // Close it behind us. The server drains its open connections before
            // it exits, and a kept-alive socket of ours would be one of the
            // things it waits on.
            .header("Connection", "close")
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                log_line("backend stop: the backend accepted the request to shut itself down");
                true
            }
            Ok(resp) => {
                log_line(&format!(
                    "backend stop: the backend would not shut itself down ({})",
                    resp.status()
                ));
                false
            }
            Err(e) => {
                log_line(&format!("backend stop: the shutdown request failed: {e}"));
                false
            }
        }
    })
}

/// Step two, POSIX only: SIGTERM.
///
/// A real request rather than a kill - the server runs its own shutdown handler
/// on it, the same one the request above reaches. It is the second step and no
/// longer the first because the request works on every platform, and it is
/// still here because a signal arrives even when the HTTP port does not answer.
///
/// Returns whether a request was actually sent.
#[cfg(not(target_os = "windows"))]
fn signal_backend_stop(pid: u32) -> bool {
    let pid_arg = pid.to_string();
    match std::process::Command::new("kill")
        .args(["-TERM", pid_arg.as_str()])
        .status()
    {
        Ok(status) => {
            log_line(&format!(
                "backend stop: SIGTERM to pid {pid} exited {status}"
            ));
            status.success()
        }
        Err(e) => {
            log_line(&format!("backend stop: could not signal pid {pid}: {e}"));
            false
        }
    }
}

/// There is no second step on Windows.
///
/// A console process whose parent has no console cannot be handed a stop
/// request by any signal Windows will deliver, which is exactly why the backend
/// serves that request over HTTP instead.
#[cfg(target_os = "windows")]
fn signal_backend_stop(_pid: u32) -> bool {
    false
}

/// Step three: stop the process tree by force.
///
/// `taskkill /T` and not the child handle alone, because `CommandChild::kill`
/// is `TerminateProcess`, which stops that one process and nothing it started.
/// The sidecar starts the embedded PostgreSQL postmaster as a child of its own,
/// and the shipped sidecar is a one-file bundle whose bootloader runs the real
/// interpreter as a further child, so the process the launcher holds a handle
/// to need not be the process holding the database open.
///
/// This is an unclean stop for PostgreSQL, and it is now the last resort rather
/// than the first move.
#[cfg(target_os = "windows")]
fn force_backend_stop(pid: u32) {
    use std::os::windows::process::CommandExt;

    /// `CREATE_NO_WINDOW`: a console process spawned from a windowed one puts a
    /// black console on screen, and a console flashing up as the app closes is
    /// the last thing a user should see of it.
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    let pid_arg = pid.to_string();
    match std::process::Command::new("taskkill")
        .args(["/PID", pid_arg.as_str(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
    {
        Ok(status) => log_line(&format!(
            "backend stop: taskkill on pid {pid} exited {status}"
        )),
        Err(e) => log_line(&format!(
            "backend stop: could not run taskkill on pid {pid}: {e}"
        )),
    }
}

/// Nothing extra to force on POSIX: the caller's `child.kill()` is SIGKILL.
#[cfg(not(target_os = "windows"))]
fn force_backend_stop(_pid: u32) {}

/// Wait for the sidecar to be observed exiting, up to `budget`.
fn wait_until_exited(exited: &Arc<AtomicBool>, budget: Duration) -> bool {
    let deadline = Instant::now() + budget;
    while !exited.load(Ordering::SeqCst) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(100));
    }
    exited.load(Ordering::SeqCst)
}

/// Stop the backend sidecar on the way out, in three steps, gentlest first.
///
/// 1. Ask the backend to shut itself down. It runs its own shutdown handler,
///    which stops the embedded PostgreSQL cluster cleanly, so the next start
///    has no write-ahead log to replay. This is what closing the app should
///    always have done, on every platform.
/// 2. SIGTERM, on POSIX, where the same handler can be reached by signal even
///    if the HTTP port cannot be reached at all.
/// 3. Force, taking the process tree with it. What was left behind before there
///    was any tree stop kept running with nobody to stop it: a postmaster still
///    attached to the cluster after the app had closed, killed eventually by
///    the operating system at logoff, which is an unclean stop, which is why
///    the next launch found a cluster to recover.
///
/// Each step is logged by name, so a user's log says which of the three
/// actually stopped their backend rather than only that it stopped.
fn stop_backend(app_handle: &tauri::AppHandle) {
    let state = app_handle.state::<AppState>();
    // Announce the shutdown before causing it. Every watcher treats a backend
    // that disappears as a failure worth telling the user about, and the stop
    // below is a disappearance; without this flag, closing the app would end
    // with a message claiming the backend had crashed.
    state.shutting_down.store(true, Ordering::SeqCst);
    let exited = state.backend_exited.clone();
    // Copy what the steps below need out of managed state first, each in its
    // own statement, so no MutexGuard temporary is still alive when `state`
    // goes out of scope. Holding a guard across the body borrowed `state` too
    // long and failed to compile (E0597) in the release build.
    let port = *state.backend_port.lock().unwrap();
    let token = state.shutdown_token.clone();
    let child = state.backend_child.lock().unwrap().take();
    let child = match child {
        Some(child) => child,
        // Nothing to stop: either we attached to a backend somebody else owns,
        // or this has already run once on the way out.
        None => return,
    };

    // Read the pid BEFORE kill(), which consumes the handle.
    let pid = child.pid();
    log_line(&format!("stopping the backend sidecar (pid {pid})"));

    // Step one. `port` is Some only for a sidecar we started ourselves, so a
    // backend we merely attached to is never asked to stop.
    if let Some(port) = port {
        if ask_backend_to_stop(port, &token) && wait_until_exited(&exited, GRACEFUL_STOP_WAIT) {
            log_line("backend stop: the backend shut itself down cleanly");
            return;
        }
    }

    // Step two.
    if signal_backend_stop(pid) && wait_until_exited(&exited, BACKEND_STOP_WAIT) {
        log_line("backend stop: the backend exited after SIGTERM");
        return;
    }

    // Step three.
    log_line("backend stop: falling back to a forced stop of the process tree");
    force_backend_stop(pid);
    let _ = child.kill();
    if wait_until_exited(&exited, BACKEND_STOP_WAIT) {
        log_line("the backend sidecar has exited");
    } else {
        log_line("the backend sidecar is still running after a forced stop");
    }
}

/// Show a native, blocking failure dialog when the app cannot even be built.
///
/// On Windows this calls `MessageBoxW` directly (no Tauri window is available at
/// this point), pairing the error text with the launcher log path so the user
/// can find full diagnostics. On every other platform it is a no-op beyond the
/// log line and stderr that the caller already emitted.
#[cfg(windows)]
fn show_startup_failure_dialog(message: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, MB_ICONERROR, MB_OK, MB_SETFOREGROUND, MB_TOPMOST,
    };

    let log_hint = log_path()
        .map(|p| format!("\n\nA full diagnostic log was written to:\n{}", p.display()))
        .unwrap_or_default();
    let body = format!(
        "OpenConstructionERP could not start.\n\n{message}{log_hint}\n\n\
If this keeps happening, please send the log file to info@datadrivenconstruction.io."
    );

    let to_wide = |s: &str| -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>()
    };
    let body_w = to_wide(&body);
    let title_w = to_wide("OpenConstructionERP failed to start");

    // SAFETY: both buffers are valid, NUL-terminated UTF-16; a null HWND is the
    // documented way to show an owner-less message box.
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            body_w.as_ptr(),
            title_w.as_ptr(),
            MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST,
        );
    }
}

/// Non-Windows fallback: the log line and stderr from the caller are the whole
/// story, so there is nothing extra to do here.
#[cfg(not(windows))]
fn show_startup_failure_dialog(_message: &str) {}

#[cfg(test)]
mod tests {
    use super::*;

    /// The separators cmd.exe honours outside quotes must survive as text.
    ///
    /// These are the strings that pass `open_external_url`'s scheme test in
    /// full, so nothing upstream of the opener refuses them. Each one is a URL
    /// by every rule that function applies, and each one used to reach the
    /// command line unquoted, because Rust adds quotes only around an argument
    /// carrying a space or a tab and none of these carries either.
    #[cfg(target_os = "windows")]
    #[test]
    fn a_shell_separator_in_a_link_is_still_only_a_link() {
        for target in [
            "https://example.invalid/&calc",
            "https://example.invalid/|calc",
            "https://example.invalid/&&calc",
            "http://example.invalid/?a=1&b=2",
            "https://example.invalid/^calc",
            "mailto:info@datadrivenconstruction.io?subject=a&body=b",
        ] {
            assert!(
                target_is_safe_for_cmd(target),
                "{target} is quotable and must not be refused: the ampersand in a \
                 query string is ordinary, and quoting is what makes it harmless"
            );
        }
    }

    /// Only what can break out of the quoting is refused.
    #[cfg(target_os = "windows")]
    #[test]
    fn a_target_that_could_close_the_quote_is_refused() {
        for target in [
            "https://example.invalid/\"&calc",
            "https://example.invalid/\r\ncalc",
            "https://example.invalid/\ncalc",
        ] {
            assert!(
                !target_is_safe_for_cmd(target),
                "{target:?} carries a quote or a line break and must be refused"
            );
        }
    }

    /// The target reaches the command line inside quotes, which is the fix.
    ///
    /// This is the half `target_is_safe_for_cmd` cannot speak for. That
    /// predicate would go on returning true for an ampersand URL even if the
    /// quotes were removed tomorrow, and an ampersand URL reaching cmd.exe
    /// unquoted is the whole defect. So the assertion is on the text actually
    /// written onto the line.
    #[cfg(target_os = "windows")]
    #[test]
    fn the_target_is_written_onto_the_command_line_quoted() {
        let target = "https://example.invalid/&calc";
        let args = cmd_open_args(target);

        assert_eq!(args[0], "/c");
        assert_eq!(args[1], "start");
        assert_eq!(
            args[2], "\"\"",
            "start still needs its empty title argument"
        );
        // Written out in full rather than rebuilt with the same format! the
        // implementation uses. Mirroring the expression would make the two
        // sides move together, and an assertion that cannot disagree with the
        // code it checks is not checking it.
        assert_eq!(
            args[3], "\"https://example.invalid/&calc\"",
            "the target must be wrapped in quotes: unquoted, cmd reads the & as a \
             separator and runs the rest as a program"
        );
    }

    /// Percent signs stay allowed, and the reason is written down.
    ///
    /// Every encoded character in a URL is spelled with one, so refusing them
    /// would break far more links than it could protect. This pins the decision
    /// so a later tightening has to argue with it rather than pass silently.
    #[cfg(target_os = "windows")]
    #[test]
    fn a_percent_encoded_link_is_not_refused() {
        assert!(target_is_safe_for_cmd(
            "https://example.invalid/a%20b?q=%D0%BC%D0%B5%D1%82%D1%80"
        ));
    }

    /// Parse a health body the way both judgements do, for the tests below.
    fn body(json: &str) -> serde_json::Value {
        serde_json::from_str(json).expect("the test body has to be valid JSON")
    }

    /// Is this body one `judge_health` would open the application on?
    fn judged_ready(json: &str) -> bool {
        matches!(judge_health(json), HealthProbe::Ready)
    }

    /// Serve one canned `/api/health` body on an ephemeral loopback port.
    ///
    /// The probe under test takes a port and builds its own URL, so the only way
    /// to exercise it for real is to put something on a port. One request is all
    /// it makes, so the task ends after one.
    async fn serve_one_health_body(body: String) -> u16 {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("could not bind a loopback port");
        let port = listener.local_addr().expect("no local address").port();
        tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                use tokio::io::{AsyncReadExt, AsyncWriteExt};
                let mut scratch = [0_u8; 2048];
                let _ = socket.read(&mut scratch).await;
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
Content-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = socket.write_all(response.as_bytes()).await;
                let _ = socket.shutdown().await;
            }
        });
        port
    }

    /// A health body of our own version, so only the fault question can decide.
    fn our_backend_saying(fields: &str) -> String {
        format!(r#"{{"version":"{}",{fields}}}"#, env!("CARGO_PKG_VERSION"))
    }

    #[tokio::test]
    async fn the_attach_probe_and_the_startup_judgement_agree_on_the_same_backend() {
        // The defect, tested end to end rather than through the shared helper.
        // One backend, two decisions: may the user open the app, and may we
        // attach instead of starting another one. They used to be written apart
        // and answered differently, and the disagreement was not academic - it
        // put a second backend on a live cluster's data directory.
        //
        // This drives the real probe over a real socket, so it still fails if
        // somebody puts an independent check back inside it. The bodies all
        // carry our own version, because version equality is the one question
        // the attach probe asks and the startup judgement rightly does not.
        //
        // Writes a few lines to the launcher log, like every other call to the
        // attach probe. That is the probe being itself; there is nothing to
        // stub that would not also stub what is under test.
        let client = reqwest::Client::new();
        let bodies = [
            r#""status":"healthy","database":"ok","frontend_dist_present":true"#,
            // Behind on migrations, and serving its users perfectly well.
            r#""status":"degraded","database":"ok","frontend_dist_present":true,
               "alembic_head_matches":false"#,
            // Could not heal its schema, and likewise still serving.
            r#""status":"degraded","database":"ok","frontend_dist_present":true,
               "schema_heal_failed":true"#,
            // The two that really do leave nothing working.
            r#""status":"degraded","database":"error","frontend_dist_present":true"#,
            r#""status":"degraded","database":"ok","frontend_dist_present":false"#,
        ];

        for fields in bodies {
            let body = our_backend_saying(fields);
            let port = serve_one_health_body(body.clone()).await;
            let attachable = is_our_backend_healthy(&client, port).await;
            let openable = judged_ready(&body);
            assert_eq!(
                attachable, openable,
                "the two judgements disagree about this backend: {body}"
            );
        }
    }

    #[tokio::test]
    async fn the_one_disagreement_left_is_the_one_that_belongs_there() {
        // A backend of somebody else's version is fit for its own users and
        // unfit for us, and the two answers must stay different. Reaching
        // agreement here would mean attaching to a stranger's backend and
        // serving our users its frontend and its schema.
        let client = reqwest::Client::new();
        let body = r#"{"version":"0.0.1-not-ours","status":"healthy",
            "database":"ok","frontend_dist_present":true}"#
            .to_string();
        let port = serve_one_health_body(body.clone()).await;

        assert!(judged_ready(&body), "its own users can still open it");
        assert!(
            !is_our_backend_healthy(&client, port).await,
            "we must not attach to a backend that is not our version"
        );
    }

    #[test]
    fn the_shared_question_answers_the_same_way_for_both_callers() {
        // The content of the shared decision, pinned. Agreement between the two
        // callers is now structural - both delegate here - so what is worth
        // testing is WHAT it decides, and that it does not reach agreement by
        // finding nothing wrong with anything.
        let bodies = [
            r#"{"status":"healthy","database":"ok","frontend_dist_present":true}"#,
            r#"{"status":"ok","database":"ok","frontend_dist_present":true}"#,
            // Degraded for a reason that leaves the app usable.
            r#"{"status":"degraded","database":"ok","frontend_dist_present":true,
                "alembic_head_matches":false}"#,
            r#"{"status":"degraded","database":"ok","frontend_dist_present":true,
                "schema_heal_failed":true}"#,
            // Degraded for a reason that does not.
            r#"{"status":"degraded","database":"error","frontend_dist_present":true}"#,
            r#"{"status":"degraded","database":"ok","frontend_dist_present":false}"#,
            // Fields absent, renamed or of an unexpected type.
            r#"{"status":"degraded"}"#,
            r#"{"status":"something-new","database":"error"}"#,
            r#"{}"#,
        ];

        for json in bodies {
            let ready = judged_ready(json);
            let attachable = blocking_fault(&body(json)).is_none();
            assert_eq!(
                ready, attachable,
                "the two judgements disagree about this backend: {json}"
            );
        }
    }

    #[test]
    fn a_stale_migration_head_does_not_cost_a_user_a_second_backend() {
        // The regression, named. A backend whose schema is behind reports
        // status=degraded and alembic_head_matches=false, and it is serving its
        // users. Rejecting it did not mean looking elsewhere; it meant starting
        // a second backend against the first one's data directory.
        let json = r#"{"status":"degraded","version":"1.0.0","database":"ok",
            "frontend_dist_present":true,"alembic_head_matches":false}"#;

        assert!(judged_ready(json), "the app must still open");
        assert!(
            blocking_fault(&body(json)).is_none(),
            "and the launcher must attach to it rather than start another backend"
        );
    }

    #[test]
    fn a_head_that_cannot_be_determined_is_not_a_fault() {
        // What the desktop build actually reports. It ships no migration tree,
        // so the head comparison answers null forever. Null is "I could not
        // tell", and I-could-not-tell must never be the reason a second backend
        // is started.
        for json in [
            r#"{"status":"healthy","database":"ok","frontend_dist_present":true,
                "alembic_head_matches":null}"#,
            r#"{"status":"healthy","database":"ok","frontend_dist_present":true}"#,
        ] {
            assert!(judged_ready(json), "got a fault for: {json}");
            assert!(blocking_fault(&body(json)).is_none(), "got a fault for: {json}");
        }
    }

    #[test]
    fn the_faults_that_stop_everything_still_stop_it() {
        // The other polarity. A shared question that never finds a fault would
        // pass the agreement test above and put users in front of an
        // application shell with every request inside it failing.
        let no_database = r#"{"status":"degraded","database":"error","frontend_dist_present":true}"#;
        let reason = blocking_fault(&body(no_database)).expect("a dead database is a fault");
        assert!(reason.contains("database"), "got: {reason}");
        assert!(!judged_ready(no_database));

        let no_frontend = r#"{"status":"degraded","database":"ok","frontend_dist_present":false}"#;
        let reason = blocking_fault(&body(no_frontend)).expect("no application files is a fault");
        assert!(reason.contains("application files"), "got: {reason}");
        assert!(!judged_ready(no_frontend));
    }

    #[test]
    fn an_unreadable_body_keeps_meaning_two_different_things() {
        // Not an oversight, and not to be tidied. A body that will not parse
        // means "open the user's own installation" to judge_health and "do not
        // trust this stranger" to the attach probe, because the two are asking
        // about different machines. The shared question sits below the parse in
        // both, so it never gets the chance to flatten them.
        assert!(
            judged_ready("not json at all"),
            "a body we cannot read must not hold a user out of their own install"
        );
        assert!(
            serde_json::from_str::<serde_json::Value>("not json at all").is_err(),
            "and the attach probe rejects on exactly this parse failing"
        );
    }

    #[test]
    fn the_timeout_message_names_the_step_the_backend_was_on() {
        let stage = (
            "pg".to_string(),
            "Recovering the local database".to_string(),
        );
        let quiet = startup_timeout_message(
            Some(&stage),
            &TimeoutKind::WentQuiet(Duration::from_secs(300)),
        );

        assert!(
            quiet.contains("preparing the local database"),
            "the step has to be named, got: {quiet}"
        );
        assert!(
            quiet.contains("Recovering the local database"),
            "got: {quiet}"
        );
        assert!(
            quiet.contains("5 minutes"),
            "the silence has to be quantified, got: {quiet}"
        );

        let slow = startup_timeout_message(Some(&stage), &TimeoutKind::TookTooLong);
        assert!(slow.contains("preparing the local database"), "got: {slow}");
        // A backend that kept talking is slow, not unresponsive, and the two
        // must not be described in the same words.
        assert!(!slow.contains("stopped responding"), "got: {slow}");
    }

    #[test]
    fn the_timeout_message_falls_back_when_no_step_was_reported() {
        // Nothing was ever heard from the backend, so there is nothing to name
        // and the old wording is the honest one.
        let message = startup_timeout_message(None, &TimeoutKind::TookTooLong);
        assert!(message.contains("did not start in time"), "got: {message}");
        assert!(
            message.contains("info@datadrivenconstruction.io"),
            "got: {message}"
        );
    }

    #[test]
    fn every_boot_stage_id_has_words_of_its_own() {
        // The ids the backend and the launcher actually emit. A new stage that
        // is not described here reads as the generic fallback, which is the
        // failure this whole change is about.
        for id in ["sidecar", "pg", "migrate", "model", "server", "open"] {
            assert_ne!(
                describe_stage(id),
                describe_stage("something-else"),
                "stage {id} has no words of its own"
            );
        }
    }

    #[test]
    fn progress_latches_the_last_output_and_the_last_stage() {
        let progress = BootProgress::new();
        assert!(progress.stage().is_none());

        progress.saw_stage("pg", "Starting embedded PostgreSQL");
        let (id, detail) = progress.stage().expect("a stage was seen");
        assert_eq!(id, "pg");
        assert_eq!(detail, "Starting embedded PostgreSQL");

        std::thread::sleep(Duration::from_millis(60));
        assert!(
            progress.quiet_for() >= Duration::from_millis(50),
            "silence has to accumulate while nothing is written"
        );

        progress.saw_output();
        assert!(
            progress.quiet_for() < Duration::from_millis(50),
            "any line at all has to reset the silence"
        );
        // Output is not a stage: what the backend was doing is still the last
        // step it named.
        assert_eq!(progress.stage().expect("still latched").0, "pg");
    }

    #[test]
    fn a_backend_that_exits_is_seen_to_exit_and_one_that_does_not_is_not() {
        // This is what decides which of the three stop steps the log reports.
        // If a clean shutdown were not observed, the launcher would announce a
        // forced kill after a stop that was in fact graceful - the exact kind
        // of misleading log this work exists to remove.
        let exited = Arc::new(AtomicBool::new(false));
        let flag = exited.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(150));
            flag.store(true, Ordering::SeqCst);
        });

        let started = Instant::now();
        assert!(
            wait_until_exited(&exited, Duration::from_secs(5)),
            "an exit that happens inside the budget has to be seen"
        );
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "and seen when it happens, not after the whole budget"
        );

        // The other polarity: a backend that never goes has to be reported as
        // still running, so the next step actually runs.
        let stuck = Arc::new(AtomicBool::new(false));
        let started = Instant::now();
        assert!(!wait_until_exited(&stuck, Duration::from_millis(300)));
        assert!(
            started.elapsed() >= Duration::from_millis(250),
            "giving up early would force-kill a backend still shutting down"
        );
    }
}
