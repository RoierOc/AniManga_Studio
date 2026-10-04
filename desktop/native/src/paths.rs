use std::ffi::OsString;
use std::path::PathBuf;

fn directory(base: Option<OsString>, suffix: &str) -> PathBuf {
    base.filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
        .join(suffix)
}

pub fn app_data_dir() -> PathBuf {
    directory(std::env::var_os("LOCALAPPDATA"), "AniMangaStudio")
}

pub fn webview_data_dir() -> PathBuf {
    directory(std::env::var_os("USERPROFILE"), "animanga-native").join("wv2-userdata")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn respects_selected_profile() {
        assert_eq!(directory(Some(OsString::from("profile with spaces")), "app"),
                   PathBuf::from("profile with spaces/app"));
    }

    #[test]
    fn missing_or_empty_profile_uses_temp_directory() {
        let expected = std::env::temp_dir().join("app");
        assert_eq!(directory(None, "app"), expected);
        assert_eq!(directory(Some(OsString::new()), "app"), expected);
    }
}
