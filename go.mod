module github.com/shoutcape/hypr-voice-controls

go 1.25.7

require (
	github.com/ggerganov/whisper.cpp/bindings/go v0.0.0
	github.com/go-audio/wav v1.1.0
)

require (
	github.com/BurntSushi/toml v1.6.0 // indirect
	github.com/go-audio/audio v1.0.0 // indirect
	github.com/go-audio/riff v1.0.0 // indirect
)

replace github.com/ggerganov/whisper.cpp/bindings/go => ./third_party/whisper.cpp/bindings/go
