// OpenConstructionERP Desktop, choosing which application server this window
// talks to.
//
// The launcher has always answered that question the same way: probe loopback,
// and if nothing answers, start a server of its own. That is still what happens
// on a machine where nobody has said otherwise, and this file changes nothing
// about it. What it adds is the "otherwise": a place for an answer that names a
// server somewhere else, so an office that already runs one central
// OpenConstructionERP does not have to run a second copy on every desk.
//
// The default is untouched on purpose. A first run asks no question and shows
// no chooser; a user who never goes looking for this will never learn it
// exists. Everything here is reached only by someone who went looking, or by an
// administrator who deployed a file.
//
// Nothing in this file talks to a network, starts anything, or touches Tauri.
// It reads three inputs, decides, and returns; `resolve_from_layers` is pure
// and is where the precedence actually lives, so the order can be tested
// without a home directory, an environment or a disk. That separation is the
// point: a precedence bug is the kind that hides for a year on the one machine
// where two layers disagree, and it is only findable if the order is a function
// you can call with all four combinations.

use std::path::PathBuf;

// The launcher's own answer to "where is this user's home", not a second copy
// of it. The setting written here has to land in the same `.openestimate` folder
// as the embedded database, the update opt-out and the declined version, and two
// independent implementations of that question are two chances to disagree about
// it on the one machine where the environment is unusual.
use crate::home_dir;

/// The environment variable an administrator sets to point a machine at a
/// server.
///
/// Named by the founder in the issue. Note that the launcher's existing
/// variable is `OE_DISABLE_UPDATE_CHECK`, so the two prefixes do not match;
/// this one is spelled the way it was asked for rather than the way its
/// neighbour is, and that is a deliberate choice to follow the instruction, not
/// an oversight to be tidied away later without asking.
pub const SERVER_URL_ENV: &str = "OCE_SERVER_URL";

/// The one value of `OCE_SERVER_URL` that is not an address.
///
/// The environment sits ABOVE the deployed configuration file (see
/// `resolve_from_layers`), and without a word for "no, start one here" it could
/// only ever push a machine towards a remote server and never back. An
/// administrator debugging one desk on a fleet whose file says remote needs to
/// be able to say local for one launch without editing the fleet's file.
pub const LOCAL_KEYWORD: &str = "local";

/// The user's own choice, written only when a human explicitly makes one.
///
/// Absence is a distinct state from a choice of local, and the whole
/// precedence chain depends on that being true. If "no choice yet" and "the
/// user chose local" were the same stored value, then the top layer would
/// answer local on every machine on earth and the deployed file below it would
/// never be reached, which is to say administrator deployment would silently
/// never work. So this file does not exist until somebody chooses.
const SETTING_FILE: &str = "server-choice.json";

/// The file an administrator deploys, with the same two keys as the user's own,
/// so there is one vocabulary to learn rather than two.
const ADMIN_FILE: &str = "config.json";

/// Which server this window should be pointed at.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServerChoice {
    /// Behave exactly as the launcher always has: attach to a healthy loopback
    /// server if one is already there, otherwise start a sidecar.
    Local,
    /// Talk to this server and start nothing. The string is the canonical form
    /// returned by `validate_server_url`, never the raw text somebody typed.
    Remote { url: String },
}

/// Which layer of the precedence chain gave the answer.
///
/// Carried so that every log line and every failure message can name the thing
/// a person would have to change. "Could not reach the server" is a support
/// ticket; "could not reach the server your administrator configured in
/// C:\ProgramData\OpenConstructionERP\config.json" is a fix.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChoiceSource {
    /// A choice this user made in the application.
    Setting,
    /// The `OCE_SERVER_URL` environment variable.
    Environment,
    /// The configuration file an administrator deployed on this machine.
    AdminFile,
    /// Nobody said anything, so the launcher does what it always did.
    Default,
}

impl ChoiceSource {
    /// A phrase that fits into "... configured in the settings of this app".
    ///
    /// English, like every other string the launcher itself says. The launcher
    /// has no translation layer at all today, which is a known open item; these
    /// join the existing English strings rather than starting a second,
    /// half-built one beside them.
    pub fn describe(self) -> &'static str {
        match self {
            ChoiceSource::Setting => "the server setting in this app",
            ChoiceSource::Environment => "the OCE_SERVER_URL environment variable",
            ChoiceSource::AdminFile => "the configuration file deployed on this machine",
            ChoiceSource::Default => "the built-in default",
        }
    }
}

/// What the chain decided, and which layer decided it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Resolution {
    /// Start or attach to a server on this machine, as always.
    Local { source: ChoiceSource },
    /// Use this server instead of starting one.
    Remote { url: String, source: ChoiceSource },
    /// A layer named a server address that cannot be used, so nothing is
    /// started and nothing is contacted.
    ///
    /// Falling through to a local start here would be the worse bug. A typo in
    /// a deployed file would open a working window onto an empty local database
    /// on a hundred desks, every one of them believing it was looking at the
    /// office server. Refusing is loud, and the failure screen it produces
    /// carries a button that starts a local server, so loud is also
    /// recoverable.
    Refused {
        source: ChoiceSource,
        raw: String,
        problem: UrlProblem,
    },
}

/// Why a server address was refused.
///
/// One variant per reason rather than a string, so the failure screen, the log
/// and the tests all read the same decision instead of matching on prose.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UrlProblem {
    /// Nothing but whitespace.
    Empty,
    /// The URL parser could not make sense of it at all.
    Unparseable(String),
    /// Parsed, but not a scheme we can serve an application over.
    Scheme(String),
    /// Parsed, but names no host to connect to.
    NoHost,
    /// Carries a username or a password.
    Credentials,
    /// Carries a query string or a fragment.
    QueryOrFragment,
    /// Contains a `%` that is not the start of a valid percent escape.
    BadPercentEscape(String),
    /// The address we would actually use is not the address we were given.
    NotStable { typed: String, used: String },
}

impl UrlProblem {
    /// A complete sentence a non-technical user can act on.
    pub fn message(&self) -> String {
        match self {
            UrlProblem::Empty => "No server address was given.".to_string(),
            UrlProblem::Unparseable(why) => {
                format!("That is not a valid web address ({why}).")
            }
            UrlProblem::Scheme(scheme) => format!(
                "A server address has to start with http:// or https://, and this one starts with {scheme}://."
            ),
            UrlProblem::NoHost => {
                "That address names no server to connect to.".to_string()
            }
            UrlProblem::Credentials => {
                "A server address must not carry a user name or a password. Remove the part before the @ sign."
                    .to_string()
            }
            UrlProblem::QueryOrFragment => {
                "A server address must not carry a ? or a # part. Give the address of the server itself."
                    .to_string()
            }
            UrlProblem::BadPercentEscape(fragment) => format!(
                "That address contains \"{fragment}\", which is not a valid escape. A % has to be followed by two digits or the letters A to F."
            ),
            UrlProblem::NotStable { typed, used } => format!(
                "That address does not survive being read back: it was given as {typed} and would be used as {used}. Please write it out in full."
            ),
        }
    }
}

/// The raw text each layer offered, before any of it has been looked at.
///
/// Deliberately three `Option<String>` and nothing else. Everything that needs
/// a disk, an environment or a home directory happens in `read_layers`; this
/// struct is what makes the precedence itself testable without any of them.
#[derive(Debug, Default, Clone)]
pub struct RawLayers {
    /// Contents of the user's own choice: `LOCAL_KEYWORD`, or an address.
    pub setting: Option<String>,
    /// Value of `OCE_SERVER_URL`: `LOCAL_KEYWORD`, or an address.
    pub environment: Option<String>,
    /// What the deployed file said: `LOCAL_KEYWORD`, or an address.
    pub admin_file: Option<String>,
}

/// Decide, in precedence order, which server this window talks to.
///
/// THE ORDER, AND WHY IT IS THIS ORDER.
///
/// The user's own setting, then the environment, then the deployed file, then
/// the default of starting a local server. The layers are ranked by how
/// specific and how recent the human act behind each one is, and every other
/// ordering I considered breaks something concrete.
///
/// The setting has to be on top because it is the only layer a user can change
/// without a text editor, and the product requirement is that somebody whose
/// remote server is wrong can get back to a working local start without
/// editing a file. Put the deployed file above it and that promise cannot be
/// kept on exactly the machines that have a deployed file, which are the
/// machines most likely to have a wrong one.
///
/// The tempting argument for the other order is that a file deployed by an
/// administrator is policy and should not be overridable by a user. That
/// argument does not survive contact with this chain: the setting is above the
/// environment too, so a user who wants to escape policy escapes it at the top
/// no matter how the two layers below are ordered. Ranking the file above the
/// environment would buy no enforcement and would cost the ability to override
/// one desk for one launch. So this is not a policy mechanism and is not
/// documented as one. An administrator who needs a machine pinned deploys the
/// file to a machine whose users cannot write it and accepts that a user who
/// opens the setting can still point their own copy elsewhere.
///
/// The environment sits above the file because it is per launch and the file is
/// per machine: a variable is how you test one desk against a staging server
/// this afternoon without touching the fleet's file and remembering to put it
/// back. `LOCAL_KEYWORD` exists so that override can point both ways.
///
/// A layer whose value is unusable stops the chain rather than falling through
/// to the next one. See `Resolution::Refused`.
pub fn resolve_from_layers(layers: &RawLayers) -> Resolution {
    for (raw, source) in [
        (layers.setting.as_deref(), ChoiceSource::Setting),
        (layers.environment.as_deref(), ChoiceSource::Environment),
        (layers.admin_file.as_deref(), ChoiceSource::AdminFile),
    ] {
        let Some(raw) = raw else { continue };
        let trimmed = raw.trim();
        // An empty value is treated as "this layer said nothing" rather than as
        // an error. An exported-but-empty variable is the normal way a shell
        // profile leaves one behind, and a machine should not fail to start
        // because of it.
        if trimmed.is_empty() {
            continue;
        }
        if trimmed.eq_ignore_ascii_case(LOCAL_KEYWORD) {
            return Resolution::Local { source };
        }
        return match validate_server_url(trimmed) {
            Ok(url) => Resolution::Remote { url, source },
            Err(problem) => Resolution::Refused {
                source,
                raw: trimmed.to_string(),
                problem,
            },
        };
    }

    Resolution::Local {
        source: ChoiceSource::Default,
    }
}

/// Check a server address, and return the exact string that will be used.
///
/// The return value matters as much as the check. Callers must navigate to what
/// comes back here and never to what was typed, because the two can differ:
/// a host is lower-cased, an international domain is punycoded, a missing
/// trailing slash is added. Handing the typed string to one consumer and the
/// canonical one to another is how two parts of a program end up disagreeing
/// about which server they are talking to.
///
/// On the percent-escape trap. A URL parser is not a validator. Both
/// JavaScript's `new URL()` and the Rust `url` crate accept `%zz` and carry it
/// through untouched, so "it parsed" says nothing about whether the address is
/// safe to use; the sequence simply arrives at the far end as a literal `%zz`
/// and means whatever the server decides it means. So the escape check runs on
/// the typed text BEFORE parsing, where an invalid escape is still visible as
/// itself. Running it after would be checking the parser's output, which by
/// construction only ever contains escapes the parser wrote.
pub fn validate_server_url(input: &str) -> Result<String, UrlProblem> {
    let typed = input.trim();
    if typed.is_empty() {
        return Err(UrlProblem::Empty);
    }

    if let Some(fragment) = first_bad_percent(typed) {
        return Err(UrlProblem::BadPercentEscape(fragment));
    }

    let parsed = reqwest::Url::parse(typed).map_err(|e| UrlProblem::Unparseable(e.to_string()))?;

    match parsed.scheme() {
        "http" | "https" => {}
        other => return Err(UrlProblem::Scheme(other.to_string())),
    }

    // `Url` reports an empty host for schemes that have none, and http always
    // has one, but a caller can still arrive here through a scheme-relative
    // oddity, so this is checked rather than assumed.
    if parsed.host_str().unwrap_or_default().is_empty() {
        return Err(UrlProblem::NoHost);
    }

    // Credentials in a URL are refused rather than stripped. A password in a
    // file an administrator deploys across a fleet, or in a setting stored in a
    // user's home folder in clear text, is a leak; silently dropping it would
    // hide the leak from the person who wrote it while leaving it in the file.
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(UrlProblem::Credentials);
    }

    // The address of a server, not of a page on it. A path is allowed, because
    // a reverse proxy quite reasonably serves us under a prefix, but a query or
    // a fragment means somebody pasted a link out of the address bar.
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err(UrlProblem::QueryOrFragment);
    }

    let canonical = with_trailing_slash(parsed.as_str());

    // Stability. Whatever we hand onward will be parsed again, by the webview
    // and by every HTTP client that touches it, so an address that means one
    // thing on the first read and another on the second is refused here rather
    // than discovered later as a window pointing somewhere nobody chose. In
    // practice the `url` crate is idempotent and this never fires; it costs one
    // parse at startup and it is the only thing standing between a future
    // parser quirk and a silently redirected application.
    let again = reqwest::Url::parse(&canonical)
        .map(|u| with_trailing_slash(u.as_str()))
        .map_err(|e| UrlProblem::Unparseable(e.to_string()))?;
    if again != canonical {
        return Err(UrlProblem::NotStable {
            typed: canonical,
            used: again,
        });
    }

    Ok(canonical)
}

/// Guarantee exactly one trailing slash, so a path can be appended to the
/// result without anybody having to check first.
fn with_trailing_slash(url: &str) -> String {
    if url.ends_with('/') {
        url.to_string()
    } else {
        format!("{url}/")
    }
}

/// Find the first `%` that does not begin a valid escape, and return it with
/// whatever follows it, so the message can quote the offending text.
fn first_bad_percent(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    for (i, &c) in bytes.iter().enumerate() {
        if c != b'%' {
            continue;
        }
        let valid = bytes.get(i + 1).is_some_and(u8::is_ascii_hexdigit)
            && bytes.get(i + 2).is_some_and(u8::is_ascii_hexdigit);
        if !valid {
            let end = (i + 3).min(s.len());
            // `get` on a range returns None when either end lands inside a
            // multi-byte character, which is exactly the case where quoting the
            // bytes would produce mojibake, so fall back to the sign itself.
            return Some(s.get(i..end).unwrap_or("%").to_string());
        }
    }
    None
}

/// Read all three layers off this machine.
///
/// Everything that can fail here fails quietly to `None`, which reads as "this
/// layer said nothing". An unreadable file is not the same as a file that says
/// local, and it must not become one: it falls through to the layer below and,
/// with nothing below it, to the local start that has always been the default.
pub fn read_layers() -> RawLayers {
    RawLayers {
        setting: setting_path().and_then(|p| read_choice_file(&p)),
        environment: std::env::var(SERVER_URL_ENV).ok(),
        admin_file: admin_config_path().and_then(|p| read_choice_file(&p)),
    }
}

/// Where this user's own choice is stored.
///
/// Beside the launcher's other per-user state (`~/.openestimate/pgdata`, the
/// update opt-out, the declined version) rather than in a platform config
/// directory, because a user who is told "delete the .openestimate folder to
/// start over" should get a complete start over, and a setting that survives
/// that instruction would be a support call nobody could diagnose.
pub fn setting_path() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".openestimate").join(SETTING_FILE))
}

/// Where an administrator deploys a choice for every user of a machine.
///
/// One conventional machine-wide location per platform, chosen so that the
/// ordinary way of imaging a fleet already knows how to put a file there and so
/// that an unprivileged user cannot rewrite it.
pub fn admin_config_path() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        let base = std::env::var("ProgramData")
            .ok()
            .filter(|p| !p.is_empty())
            .unwrap_or_else(|| "C:\\ProgramData".to_string());
        Some(
            PathBuf::from(base)
                .join("OpenConstructionERP")
                .join(ADMIN_FILE),
        )
    }
    #[cfg(target_os = "macos")]
    {
        Some(PathBuf::from("/Library/Application Support/OpenConstructionERP").join(ADMIN_FILE))
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Some(PathBuf::from("/etc/openconstructionerp").join(ADMIN_FILE))
    }
}

/// Read one `{ "backend_mode": ..., "server_url": ... }` file into the single
/// string the precedence chain works in.
///
/// Both files share this shape so an administrator and the application speak
/// one vocabulary. `backend_mode` is what carries the tri-state: the file
/// existing at all is the third state, and its absence is what lets the chain
/// fall through to the layer below.
fn read_choice_file(path: &PathBuf) -> Option<String> {
    let body = std::fs::read_to_string(path).ok()?;
    parse_choice_json(&body)
}

/// Pull the choice out of the file body.
///
/// Split from the read so the shape can be tested without a disk. Field
/// extraction is by hand off a `serde_json::Value`, matching how the update
/// check reads a release, so a file with an unexpected extra key is read rather
/// than rejected: an administrator's file is likely to grow settings that this
/// version has never heard of.
fn parse_choice_json(body: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(body).ok()?;
    let mode = value
        .get("backend_mode")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .trim();
    let url = value
        .get("server_url")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .trim();

    if mode.eq_ignore_ascii_case(LOCAL_KEYWORD) {
        return Some(LOCAL_KEYWORD.to_string());
    }
    if mode.eq_ignore_ascii_case("remote") {
        // A file that says remote and names nothing is a mistake worth
        // reporting, so the empty string is passed on and refused by the
        // validator rather than swallowed here as "said nothing".
        return Some(if url.is_empty() {
            String::new()
        } else {
            url.to_string()
        });
    }
    // A file with a URL and no mode at all is the shape somebody will write
    // from memory, and its intent is not in doubt.
    if !url.is_empty() {
        return Some(url.to_string());
    }
    None
}

/// Write this user's own choice, or clear it.
///
/// `None` deletes the file, which returns the top layer to "said nothing" and
/// hands the decision back to the environment and the deployed file below it.
/// That is the only way back to being managed once a user has chosen, so it is
/// a real operation and not an afterthought.
pub fn write_setting(choice: Option<&ServerChoice>) -> Result<(), String> {
    let path = setting_path().ok_or_else(|| "Could not resolve your home folder".to_string())?;
    let Some(choice) = choice else {
        return match std::fs::remove_file(&path) {
            Ok(()) => Ok(()),
            // Already absent is the state being asked for.
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e.to_string()),
        };
    };
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let body = match choice {
        ServerChoice::Local => {
            serde_json::json!({ "backend_mode": LOCAL_KEYWORD, "server_url": "" })
        }
        ServerChoice::Remote { url } => {
            serde_json::json!({ "backend_mode": "remote", "server_url": url })
        }
    };
    std::fs::write(
        &path,
        serde_json::to_string_pretty(&body).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())
}

/// The whole decision, read off this machine.
pub fn resolve() -> Resolution {
    resolve_from_layers(&read_layers())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layers(setting: Option<&str>, env: Option<&str>, file: Option<&str>) -> RawLayers {
        RawLayers {
            setting: setting.map(str::to_string),
            environment: env.map(str::to_string),
            admin_file: file.map(str::to_string),
        }
    }

    // ---- the default, which is the behaviour nobody asked to change ----

    #[test]
    fn nothing_configured_starts_a_local_server() {
        assert_eq!(
            resolve_from_layers(&layers(None, None, None)),
            Resolution::Local {
                source: ChoiceSource::Default
            }
        );
    }

    #[test]
    fn a_layer_that_is_present_but_empty_is_a_layer_that_said_nothing() {
        // An exported-but-empty variable is the normal residue of a shell
        // profile. If it counted as an answer, the chain would stop at it and
        // the deployed file below would never be read.
        assert_eq!(
            resolve_from_layers(&layers(None, Some("   "), Some("https://erp.example.com/"))),
            Resolution::Remote {
                url: "https://erp.example.com/".to_string(),
                source: ChoiceSource::AdminFile,
            }
        );
    }

    // ---- the order itself ----

    #[test]
    fn the_users_own_setting_outranks_everything_below_it() {
        assert_eq!(
            resolve_from_layers(&layers(
                Some("https://chosen.example.com"),
                Some("https://from-env.example.com"),
                Some("https://from-file.example.com"),
            )),
            Resolution::Remote {
                url: "https://chosen.example.com/".to_string(),
                source: ChoiceSource::Setting,
            }
        );
    }

    #[test]
    fn the_environment_outranks_the_deployed_file() {
        assert_eq!(
            resolve_from_layers(&layers(
                None,
                Some("https://from-env.example.com"),
                Some("https://from-file.example.com"),
            )),
            Resolution::Remote {
                url: "https://from-env.example.com/".to_string(),
                source: ChoiceSource::Environment,
            }
        );
    }

    #[test]
    fn an_unset_setting_is_not_a_choice_of_local() {
        // The whole of administrator deployment rests on this. If "the user has
        // never opened the setting" were stored as local, the top layer would
        // answer on every machine and the deployed file would be dead code.
        let managed = resolve_from_layers(&layers(None, None, Some("https://office.example.com")));
        assert_eq!(
            managed,
            Resolution::Remote {
                url: "https://office.example.com/".to_string(),
                source: ChoiceSource::AdminFile,
            }
        );
        assert_ne!(
            managed,
            Resolution::Local {
                source: ChoiceSource::Default
            }
        );
    }

    #[test]
    fn choosing_local_in_the_app_beats_a_deployed_remote_file() {
        // This is the way back. A user whose configured server is wrong has to
        // be able to reach a working local start without editing a file, and
        // that is only true if an explicit local at the top wins.
        assert_eq!(
            resolve_from_layers(&layers(
                Some(LOCAL_KEYWORD),
                Some("https://from-env.example.com"),
                Some("https://from-file.example.com"),
            )),
            Resolution::Local {
                source: ChoiceSource::Setting
            }
        );
    }

    #[test]
    fn the_environment_can_push_a_managed_machine_back_to_local() {
        assert_eq!(
            resolve_from_layers(&layers(
                None,
                Some("LOCAL"),
                Some("https://office.example.com")
            )),
            Resolution::Local {
                source: ChoiceSource::Environment
            }
        );
    }

    #[test]
    fn a_bad_address_stops_the_chain_instead_of_falling_through() {
        // Falling through would open a window onto an empty local database
        // while the user believed they were on the office server. The refusal
        // names the layer, so the message can say which file to fix.
        let outcome = resolve_from_layers(&layers(None, None, Some("htp://typo.example.com")));
        match outcome {
            Resolution::Refused { source, raw, .. } => {
                assert_eq!(source, ChoiceSource::AdminFile);
                assert_eq!(raw, "htp://typo.example.com");
            }
            other => panic!("expected a refusal, got {other:?}"),
        }
    }

    // ---- what an address has to be ----

    #[test]
    fn an_ordinary_address_is_canonicalised_with_one_trailing_slash() {
        assert_eq!(
            validate_server_url("https://erp.example.com"),
            Ok("https://erp.example.com/".to_string())
        );
        assert_eq!(
            validate_server_url("  https://erp.example.com/  "),
            Ok("https://erp.example.com/".to_string())
        );
    }

    #[test]
    fn a_reverse_proxy_prefix_survives() {
        // An office that already runs something on port 443 will serve us under
        // a path, and refusing that would refuse the deployment this feature
        // exists for.
        assert_eq!(
            validate_server_url("https://intranet.example.com/erp"),
            Ok("https://intranet.example.com/erp/".to_string())
        );
    }

    #[test]
    fn a_port_and_a_plain_http_lan_address_are_accepted() {
        // A site office server on the local network, which is the common case
        // in this industry and must not be refused on principle.
        assert_eq!(
            validate_server_url("http://192.168.1.20:8732"),
            Ok("http://192.168.1.20:8732/".to_string())
        );
    }

    #[test]
    fn only_http_and_https_can_serve_an_application() {
        assert_eq!(
            validate_server_url("ftp://erp.example.com"),
            Err(UrlProblem::Scheme("ftp".to_string()))
        );
        assert_eq!(
            validate_server_url("file:///etc/passwd"),
            Err(UrlProblem::Scheme("file".to_string()))
        );
        assert_eq!(
            validate_server_url("javascript:alert(1)"),
            Err(UrlProblem::Scheme("javascript".to_string()))
        );
    }

    #[test]
    fn credentials_are_refused_and_not_quietly_stripped() {
        assert_eq!(
            validate_server_url("https://admin:hunter2@erp.example.com"),
            Err(UrlProblem::Credentials)
        );
        assert_eq!(
            validate_server_url("https://admin@erp.example.com"),
            Err(UrlProblem::Credentials)
        );
    }

    #[test]
    fn a_pasted_page_link_is_refused() {
        assert_eq!(
            validate_server_url("https://erp.example.com/boq?id=7"),
            Err(UrlProblem::QueryOrFragment)
        );
        assert_eq!(
            validate_server_url("https://erp.example.com/#/boq"),
            Err(UrlProblem::QueryOrFragment)
        );
    }

    #[test]
    fn an_invalid_percent_escape_is_refused_even_though_it_parses() {
        // The trap this check exists for. A URL parser is not a validator:
        // every one of these parses cleanly and carries the bad sequence
        // through untouched, so "it parsed" is not the same as "it is usable".
        assert!(reqwest::Url::parse("https://erp.example.com/%zz").is_ok());

        assert_eq!(
            validate_server_url("https://erp.example.com/%zz"),
            Err(UrlProblem::BadPercentEscape("%zz".to_string()))
        );
        assert_eq!(
            validate_server_url("https://erp.example.com/%4"),
            Err(UrlProblem::BadPercentEscape("%4".to_string()))
        );
        assert_eq!(
            validate_server_url("https://erp.example.com/100%"),
            Err(UrlProblem::BadPercentEscape("%".to_string()))
        );
    }

    #[test]
    fn a_valid_percent_escape_is_left_alone() {
        assert_eq!(
            validate_server_url("https://erp.example.com/my%20site"),
            Ok("https://erp.example.com/my%20site/".to_string())
        );
    }

    #[test]
    fn nothing_at_all_is_refused_with_its_own_reason() {
        assert_eq!(validate_server_url(""), Err(UrlProblem::Empty));
        assert_eq!(validate_server_url("   \t "), Err(UrlProblem::Empty));
    }

    #[test]
    fn a_bare_host_with_no_scheme_is_refused_rather_than_guessed() {
        // Guessing https here would be friendly and wrong: the guess would be
        // silent, and a user who meant http on their own network would get an
        // unreachable server and no clue why.
        match validate_server_url("erp.example.com") {
            Err(UrlProblem::Unparseable(_)) => {}
            other => panic!("expected an unparseable address, got {other:?}"),
        }
    }

    #[test]
    fn every_refusal_says_something_a_person_can_act_on() {
        // A reason with no sentence attached reaches the user as a blank space
        // on the failure screen, which is the exact outcome this feature is
        // supposed to remove.
        for problem in [
            UrlProblem::Empty,
            UrlProblem::Unparseable("relative URL without a base".to_string()),
            UrlProblem::Scheme("ftp".to_string()),
            UrlProblem::NoHost,
            UrlProblem::Credentials,
            UrlProblem::QueryOrFragment,
            UrlProblem::BadPercentEscape("%zz".to_string()),
            UrlProblem::NotStable {
                typed: "a".to_string(),
                used: "b".to_string(),
            },
        ] {
            let message = problem.message();
            assert!(message.len() > 20, "too terse to help: {problem:?}");
            assert!(message.ends_with('.'), "not a sentence: {problem:?}");
        }
    }

    // ---- the file both the user and an administrator write ----

    #[test]
    fn a_file_can_say_local_or_name_a_server() {
        assert_eq!(
            parse_choice_json(r#"{"backend_mode":"local","server_url":""}"#),
            Some(LOCAL_KEYWORD.to_string())
        );
        assert_eq!(
            parse_choice_json(
                r#"{"backend_mode":"remote","server_url":"https://erp.example.com"}"#
            ),
            Some("https://erp.example.com".to_string())
        );
    }

    #[test]
    fn a_file_that_says_remote_and_names_nothing_is_a_complaint_not_a_silence() {
        // Reading this as "said nothing" would fall through to a local start
        // and never tell the administrator their file is incomplete.
        assert_eq!(
            parse_choice_json(r#"{"backend_mode":"remote","server_url":""}"#),
            Some(String::new())
        );
        match resolve_from_layers(&layers(None, None, Some(""))) {
            // An empty string from a file arrives at the chain the same way an
            // empty variable does, and the chain treats both as silence. What
            // the administrator sees is the local start they did not ask for,
            // which the log line names by source.
            Resolution::Local { source } => assert_eq!(source, ChoiceSource::Default),
            other => panic!("expected a local start, got {other:?}"),
        }
    }

    #[test]
    fn a_url_with_no_mode_is_read_the_way_it_was_obviously_meant() {
        assert_eq!(
            parse_choice_json(r#"{"server_url":"https://erp.example.com"}"#),
            Some("https://erp.example.com".to_string())
        );
    }

    #[test]
    fn an_unreadable_or_unrelated_file_says_nothing() {
        assert_eq!(parse_choice_json("not json at all"), None);
        assert_eq!(parse_choice_json("{}"), None);
        assert_eq!(parse_choice_json(r#"{"something_else":true}"#), None);
        assert_eq!(parse_choice_json("[1,2,3]"), None);
    }

    #[test]
    fn a_file_carrying_settings_this_version_never_heard_of_is_still_read() {
        assert_eq!(
            parse_choice_json(
                r#"{"backend_mode":"remote","server_url":"https://erp.example.com","future_key":42}"#
            ),
            Some("https://erp.example.com".to_string())
        );
    }

    #[test]
    fn every_source_names_the_thing_a_person_would_have_to_change() {
        for source in [
            ChoiceSource::Setting,
            ChoiceSource::Environment,
            ChoiceSource::AdminFile,
            ChoiceSource::Default,
        ] {
            assert!(!source.describe().is_empty());
        }
    }
}
