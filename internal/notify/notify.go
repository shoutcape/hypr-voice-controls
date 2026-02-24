// Package notify sends desktop notifications via hyprctl (preferred) or
// notify-send (fallback). Failures are non-fatal — a missing notification
// is never worth crashing the daemon over.
package notify

import (
	"fmt"
	"os/exec"
	"strings"
	"sync"
)

// Level controls the icon and colour of the notification.
type Level int

const (
	Info    Level = iota // blue  — neutral status
	Success              // green — transcription pasted
	Error                // red   — something went wrong
)

// tool cache so we call shutil.which equivalent only once.
var (
	toolOnce  sync.Once
	useHypr   bool
	useNotify bool
)

func detectTools() {
	toolOnce.Do(func() {
		useHypr = hasCmd("hyprctl")
		useNotify = hasCmd("notify-send")
	})
}

// Send dispatches a desktop notification. Errors are returned but callers
// should treat them as informational — never block the main flow on notify.
func Send(level Level, message string) error {
	detectTools()

	if useHypr {
		return sendHyprctl(level, message)
	}
	if useNotify {
		return sendNotifySend(level, message)
	}
	return nil // no notification tool available — silently skip
}

// sendHyprctl uses `hyprctl notify <icon> <ms> <color> <msg>`.
//
// Icons: -1=none, 0=warning, 1=info, 2=hint, 3=error, 4=confused, 5=ok
// Colors: rgb(rrggbb) hex string
func sendHyprctl(level Level, message string) error {
	icon, color := hyprParams(level)
	cmd := exec.Command("hyprctl", "notify",
		fmt.Sprintf("%d", icon),
		"3000",
		color,
		message,
	)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("hyprctl notify: %w — %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// sendNotifySend uses the standard notify-send utility as a fallback.
func sendNotifySend(level Level, message string) error {
	urgency := notifyUrgency(level)
	cmd := exec.Command("notify-send",
		"--urgency", urgency,
		"--expire-time", "3000",
		"voice-controls",
		message,
	)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("notify-send: %w — %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// hyprParams returns the hyprctl icon id and rgb color for a level.
func hyprParams(level Level) (icon int, color string) {
	switch level {
	case Success:
		return 5, "rgb(00cc44)" // green, ok icon
	case Error:
		return 3, "rgb(ff4444)" // red, error icon
	default: // Info
		return 1, "rgb(4499ff)" // blue, info icon
	}
}

// notifyUrgency maps Level to a notify-send urgency string.
func notifyUrgency(level Level) string {
	switch level {
	case Error:
		return "critical"
	case Success:
		return "normal"
	default:
		return "low"
	}
}

func hasCmd(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}
