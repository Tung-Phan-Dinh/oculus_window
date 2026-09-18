//! The application menu.
//!
//! It exists for the window-level shortcuts. macOS gives the menu bar first
//! refusal on every ⌘-key, before the key reaches any webview, so ⌘W, ⌘T and
//! ⌘K have to be menu items: a `keydown` listener in the frontend never sees
//! them, and while a browser tab's native page holds focus (`browser.rs`) the
//! app's own webview sees no keys at all. For the palette that is the point —
//! ⌘K is how you get *out* of a browser tab. Tauri's default menu spends ⌘W on
//! Close Window, which a tabbed window wants for the tab — so the whole menu is
//! built here instead, with Close Window moved to ⇧⌘W.
//!
//! Windows WebView2 consumes accelerator keys before the window menu sees
//! them. Each webview forwards the app's shortcuts to the same dispatcher.
//!
//! The items only emit; the frontend owns what they mean — the strip owns what
//! a tab is (`app/src/components/tabs/TopTabBar.tsx`), the palette owns what
//! search is (`app/src/components/palette/CommandPalette.tsx`).

#[cfg(target_os = "macos")]
use tauri::menu::AboutMetadata;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Emitter, Manager, Runtime};

const SEARCH: &str = "search";
const NEW_TAB: &str = "new-tab";
const CLOSE_TAB: &str = "close-tab";
const CLOSE_WINDOW: &str = "close-window";
const FULLSCREEN: &str = "toggle-fullscreen";

/// Events shared by menu clicks and native WebView2 accelerators.
pub const SEARCH_EVENT: &str = "menu-search";
pub const NEW_TAB_EVENT: &str = "menu-new-tab";
pub const CLOSE_TAB_EVENT: &str = "menu-close-tab";

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    #[cfg(target_os = "macos")]
    let pkg = app.package_info();
    #[cfg(target_os = "macos")]
    let about = AboutMetadata {
        name: Some(pkg.name.clone()),
        version: Some(pkg.version.to_string()),
        ..Default::default()
    };

    let file = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &MenuItem::with_id(app, SEARCH, "Search…", true, Some("CmdOrCtrl+K"))?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, NEW_TAB, "New Tab", true, Some("CmdOrCtrl+T"))?,
            &MenuItem::with_id(app, CLOSE_TAB, "Close Tab", true, Some("CmdOrCtrl+W"))?,
            &PredefinedMenuItem::separator(app)?,
            // Not the predefined item: muda nails ⌘W onto that one, which is
            // the key we just spent on the tab.
            &MenuItem::with_id(
                app,
                CLOSE_WINDOW,
                "Close Window",
                true,
                Some("Shift+CmdOrCtrl+W"),
            )?,
        ],
    )?;

    // Copy/paste on macOS are menu key equivalents like any other — without
    // this submenu ⌘C and ⌘V stop working in every text field in the app.
    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;

    let window = Submenu::with_items(
        app,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::maximize(app, None)?,
            #[cfg(target_os = "windows")]
            &MenuItem::with_id(app, FULLSCREEN, "Toggle Full Screen", true, Some("F11"))?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::separator(app)?,
            #[cfg(target_os = "macos")]
            &PredefinedMenuItem::fullscreen(app, None)?,
        ],
    )?;

    Menu::with_items(
        app,
        &[
            #[cfg(target_os = "macos")]
            &Submenu::with_items(
                app,
                pkg.name.clone(),
                true,
                &[
                    &PredefinedMenuItem::about(app, None, Some(about))?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::services(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::hide(app, None)?,
                    &PredefinedMenuItem::hide_others(app, None)?,
                    &PredefinedMenuItem::show_all(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::quit(app, None)?,
                ],
            )?,
            &file,
            &edit,
            &window,
        ],
    )
}

pub fn handle<R: Runtime>(app: &AppHandle<R>, event: MenuEvent) {
    dispatch(app, event.id().as_ref());
}

fn dispatch<R: Runtime>(app: &AppHandle<R>, id: &str) {
    match id {
        SEARCH => {
            app.emit(SEARCH_EVENT, ()).ok();
        }
        NEW_TAB => {
            app.emit(NEW_TAB_EVENT, ()).ok();
        }
        CLOSE_TAB => {
            app.emit(CLOSE_TAB_EVENT, ()).ok();
        }
        CLOSE_WINDOW => {
            if let Some(window) = app.get_focused_window() {
                window.close().ok();
            }
        }
        FULLSCREEN => {
            app.emit("menu-toggle-fullscreen", ()).ok();
        }
        _ => {}
    }
}

/// WebView2 owns the keyboard while either the main UI or a remote page has
/// focus, so Windows menu accelerators alone never reach the application.
/// Install once per controller; WebView2 releases the callback on close.
#[cfg(target_os = "windows")]
pub fn attach_windows_accelerators(webview: &tauri::Webview<tauri::Wry>) {
    use webview2_com::AcceleratorKeyPressedEventHandler;
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN,
        COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN,
    };
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        GetKeyState, VK_CONTROL, VK_LWIN, VK_MENU, VK_RWIN, VK_SHIFT,
    };

    let app = webview.app_handle().clone();
    if let Err(error) = webview.with_webview(move |platform| unsafe {
        let handler = AcceleratorKeyPressedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else { return Ok(()); };
            let mut kind = Default::default();
            args.KeyEventKind(&mut kind)?;
            if kind != COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN
                && kind != COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN
            {
                return Ok(());
            }
            let mut key = 0;
            args.VirtualKey(&mut key)?;
            let pressed = |key: u16| GetKeyState(key as i32) < 0;
            let Some(action) = windows_accelerator(
                key,
                pressed(VK_CONTROL),
                pressed(VK_SHIFT),
                pressed(VK_MENU),
                pressed(VK_LWIN) || pressed(VK_RWIN),
            ) else { return Ok(()); };
            // Release the browser's synchronous input callback before any
            // app action. Repeated presses are consumed but not dispatched.
            args.SetHandled(true)?;
            let mut status = Default::default();
            args.PhysicalKeyStatus(&mut status)?;
            if !status.WasKeyDown.as_bool() {
                let app = app.clone();
                std::thread::spawn(move || dispatch(&app, action));
            }
            Ok(())
        }));
        let mut token = 0;
        if let Err(error) = platform.controller().add_AcceleratorKeyPressed(&handler, &mut token) {
            eprintln!("[oculus] cannot register WebView2 shortcuts: {error}");
        }
    }) {
        eprintln!("[oculus] cannot access WebView2 shortcuts: {error}");
    }
}

#[cfg(any(target_os = "windows", test))]
fn windows_accelerator(key: u32, ctrl: bool, shift: bool, alt: bool, win: bool) -> Option<&'static str> {
    if alt || win { return None; }
    match (key, ctrl, shift) {
        (0x7A, false, false) => Some(FULLSCREEN), // F11
        (0x4B, true, false) => Some(SEARCH),
        (0x54, true, false) => Some(NEW_TAB),
        (0x57, true, false) => Some(CLOSE_TAB),
        (0x57, true, true) => Some(CLOSE_WINDOW),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_shortcuts_match_menu_and_leave_other_keys_to_the_page() {
        assert_eq!(windows_accelerator(0x7A, false, false, false, false), Some(FULLSCREEN));
        assert_eq!(windows_accelerator(0x4B, true, false, false, false), Some(SEARCH));
        assert_eq!(windows_accelerator(0x54, true, false, false, false), Some(NEW_TAB));
        assert_eq!(windows_accelerator(0x57, true, false, false, false), Some(CLOSE_TAB));
        assert_eq!(windows_accelerator(0x57, true, true, false, false), Some(CLOSE_WINDOW));
        for (key, ctrl, shift, alt, win) in [
            (0x4B, false, false, false, false),
            (0x4B, true, true, false, false),
            (0x4B, true, false, true, false),
            (0x4B, true, false, false, true),
            (0x7A, true, false, false, false),
            (0x43, true, false, false, false), // Copy remains native.
            (0x1B, false, false, false, false), // Escape remains page/dialog owned.
        ] {
            assert_eq!(windows_accelerator(key, ctrl, shift, alt, win), None);
        }
    }
}
