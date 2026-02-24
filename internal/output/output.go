// Package output handles injecting transcribed text into the active Wayland
// application. Strategy: copy to clipboard via wl-copy, then trigger paste
// via hyprctl dispatch sendshortcut.
package output

import (
	"bytes"
	"fmt"
	"os/exec"
	"strings"
	"unicode"

	"github.com/shoutcape/hypr-voice-controls/internal/config"
)

// Paste sanitizes text, copies it to the Wayland clipboard, and triggers
// the configured paste shortcut in the currently focused application.
func Paste(cfg *config.Config, text string) error {
	clean := Sanitize(text)
	if clean == "" {
		return nil
	}

	if err := copyToClipboard(clean); err != nil {
		return fmt.Errorf("clipboard copy failed: %w", err)
	}

	if err := triggerPaste(cfg.PasteShortcut); err != nil {
		return fmt.Errorf("paste shortcut failed: %w", err)
	}

	return nil
}

// Sanitize cleans transcribed text before injection:
//   - strips ASCII control characters (except space)
//   - collapses runs of whitespace into single spaces
//   - trims leading/trailing whitespace
func Sanitize(text string) string {
	filtered := make([]rune, 0, len(text))
	for _, r := range text {
		if r != ' ' && unicode.IsControl(r) {
			continue
		}
		filtered = append(filtered, r)
	}

	var sb strings.Builder
	sb.Grow(len(filtered))

	prevSpace := false
	for i, r := range filtered {
		// Collapse consecutive whitespace.
		isSpace := unicode.IsSpace(r)
		if isSpace {
			if !prevSpace {
				sb.WriteRune(' ')
			}
			prevSpace = true
			continue
		}
		prevSpace = false
		sb.WriteRune(r)

		if (r == '.' || r == '!' || r == '?') && i+1 < len(filtered) {
			next := filtered[i+1]
			if !unicode.IsSpace(next) && unicode.IsLetter(next) {
				sb.WriteRune(' ')
				prevSpace = true
			}
		}
	}

	return strings.TrimSpace(sb.String())
}

// copyToClipboard writes text to the Wayland clipboard via wl-copy.
func copyToClipboard(text string) error {
	cmd := exec.Command("wl-copy")
	cmd.Stdin = bytes.NewBufferString(text)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("wl-copy: %w", err)
	}
	return nil
}

// triggerPaste sends the paste shortcut via hyprctl dispatch sendshortcut.
// shortcut format: "MODIFIERS,KEY," e.g. "CTRL SHIFT,V,"
func triggerPaste(shortcut string) error {
	cmd := exec.Command("hyprctl", "dispatch", "sendshortcut", shortcut)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("hyprctl dispatch: %w — %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}
