// Package ipc defines the JSON-line protocol spoken between the voice-controls
// client and daemon over a Unix domain socket.
//
// Each message is a single JSON object terminated by a newline (\n).
// The client sends a Request; the daemon replies with a Response.
//
// Wire format examples:
//
//	→ {"action":"dictate-start"}
//	← {"rc":0,"msg":"recording started"}
//
//	→ {"action":"dictate-stop"}
//	← {"rc":0,"msg":"hello world"}
//
//	→ {"action":"ping"}
//	← {"rc":0,"msg":"pong"}
package ipc

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"time"
)

// Request is sent by the client to the daemon.
type Request struct {
	Action string `json:"action"`
}

// Response is sent by the daemon back to the client.
// RC == 0 means success; non-zero means failure.
// Msg carries the transcribed text on dictate-stop success,
// or a human-readable error description on failure.
type Response struct {
	RC  int    `json:"rc"`
	Msg string `json:"msg,omitempty"`
}

const (
	// ReadyMsg is written to stdout by the daemon once it is fully
	// initialised and accepting connections. The client waits for this
	// before sending the first request when auto-starting the daemon.
	ReadyMsg = "READY"

	// MaxLineBytes is the largest JSON line we will read in either direction.
	// 64 KB is far more than any realistic request or response needs.
	MaxLineBytes = 64 * 1024

	// DialTimeout is how long the client waits for an initial connection.
	DialTimeout = 400 * time.Millisecond

	// ReadyTimeout is how long the client waits for the daemon's READY
	// signal when it has just auto-started it.
	ReadyTimeout = 30 * time.Second

	// ResponseTimeout is how long the client waits for a response after
	// sending a request (covers recording + transcription time).
	ResponseTimeout = 180 * time.Second
)

// Send marshals req and writes it as a JSON line to conn.
func Send(conn net.Conn, req *Request) error {
	data, err := json.Marshal(req)
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}
	data = append(data, '\n')
	_, err = conn.Write(data)
	return err
}

// Recv reads one JSON line from conn and unmarshals it into resp.
func Recv(conn net.Conn, resp *Response) error {
	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, MaxLineBytes), MaxLineBytes)
	if !scanner.Scan() {
		if err := scanner.Err(); err != nil {
			return fmt.Errorf("read response: %w", err)
		}
		return fmt.Errorf("connection closed before response")
	}
	return json.Unmarshal(scanner.Bytes(), resp)
}

// WriteResponse marshals resp and writes it as a JSON line to conn.
func WriteResponse(conn net.Conn, resp *Response) error {
	data, err := json.Marshal(resp)
	if err != nil {
		return fmt.Errorf("marshal response: %w", err)
	}
	data = append(data, '\n')
	_, err = conn.Write(data)
	return err
}

// ReadRequest reads one JSON line from conn and unmarshals it into a Request.
func ReadRequest(conn net.Conn) (*Request, error) {
	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, MaxLineBytes), MaxLineBytes)
	if !scanner.Scan() {
		if err := scanner.Err(); err != nil {
			return nil, fmt.Errorf("read request: %w", err)
		}
		return nil, fmt.Errorf("client closed connection")
	}
	var req Request
	if err := json.Unmarshal(scanner.Bytes(), &req); err != nil {
		return nil, fmt.Errorf("unmarshal request: %w", err)
	}
	return &req, nil
}

// OK is a convenience constructor for a successful response.
func OK(msg string) *Response { return &Response{RC: 0, Msg: msg} }

// Err is a convenience constructor for an error response.
func Err(msg string) *Response { return &Response{RC: 1, Msg: msg} }
