# hypr-voice-controls Makefile
# Orchestrates whisper.cpp compilation and Go binary build.

# ── Configurable paths ────────────────────────────────────────────
WHISPER_DIR    := third_party/whisper.cpp
WHISPER_BUILD  := $(WHISPER_DIR)/build_go
WHISPER_REPO   := https://github.com/ggml-org/whisper.cpp.git
WHISPER_REF    := master

INCLUDE_PATH   := $(abspath $(WHISPER_DIR)/include):$(abspath $(WHISPER_DIR)/ggml/include)
LIBRARY_PATH   := $(abspath $(WHISPER_BUILD)/src):$(abspath $(WHISPER_BUILD)/ggml/src)

MODELS_DIR     := models
BINARY         := build/voice-controls

UNAME_M        := $(shell uname -m)

# ── CUDA support ──────────────────────────────────────────────────
# Set GGML_CUDA=1 to enable CUDA acceleration.
#   make build GGML_CUDA=1
#   make build-cuda          (shorthand)
ifdef GGML_CUDA
  CUDA_PATH      ?= /usr/local/cuda
  CMAKE_CUDA     := -DGGML_CUDA=ON
  LIBRARY_PATH   := $(LIBRARY_PATH):$(CUDA_PATH)/targets/$(UNAME_M)-linux/lib
  BUILD_FLAGS    := -ldflags "-extldflags '-lcudart -lcuda -lcublas'"
else
  CMAKE_CUDA     :=
  BUILD_FLAGS    :=
endif

# ── Phony targets ─────────────────────────────────────────────────
.PHONY: all build build-cuda build-wakeword-bridge install clean clean-all whisper clone test smoke lint fmt help check-portaudio check-onnxruntime build-wakeword-smoke

all: build

help:
	@echo "Targets:"
	@echo "  build                Build the voice-controls binary (CPU)"
	@echo "  build-cuda           Build with CUDA GPU acceleration"
	@echo "  build-wakeword-bridge Build the voxtype wakeword bridge binary"
	@echo "  build-wakeword-smoke Build the wakeword pipeline smoke-test binary"
	@echo "  whisper              Compile libwhisper.a from whisper.cpp"
	@echo "  clone                Clone whisper.cpp into third_party/"
	@echo "  model                Download the default .en model"
	@echo "  smoke                Run STT smoke test (requires model)"
	@echo "  test                 Run Go tests"
	@echo "  lint                 Run Go vet"
	@echo "  fmt                  Format Go source"
	@echo "  install              Install binary, config, model and systemd service"
	@echo "  clean                Remove build artifacts"
	@echo "  clean-all            Remove build artifacts and whisper.cpp clone"

# ── System dependency check ───────────────────────────────────────
# Verifies portaudio19-dev (Debian/Ubuntu) or portaudio (Arch) is installed.
# Only runs when building or testing — not for clean/fmt/help.
check-portaudio:
	@pkg-config --exists portaudio-2.0 2>/dev/null || \
		(echo "ERROR: PortAudio development headers not found."; \
		 echo "  Arch:          sudo pacman -S portaudio"; \
		 echo "  Debian/Ubuntu: sudo apt install portaudio19-dev"; \
		 exit 1)

# ── ONNX Runtime check ────────────────────────────────────────
# Required only for wakeword detection. Warns if not present (non-fatal
# for the main build since wakeword is opt-in at runtime).
check-onnxruntime:
	@ls /usr/lib/libonnxruntime.so 2>/dev/null || \
	 ls /usr/local/lib/libonnxruntime.so 2>/dev/null || \
		(echo "WARNING: libonnxruntime.so not found — wakeword detection will be unavailable."; \
		 echo "  Arch (CPU): sudo pacman -S onnxruntime-cpu"; \
		 echo "  Arch (CUDA): sudo pacman -S onnxruntime-cuda")

# ── Clone whisper.cpp ─────────────────────────────────────────────
clone: $(WHISPER_DIR)/CMakeLists.txt

$(WHISPER_DIR)/CMakeLists.txt:
	@echo "==> Cloning whisper.cpp ($(WHISPER_REF))..."
	git clone --depth 1 --branch $(WHISPER_REF) $(WHISPER_REPO) $(WHISPER_DIR)

# ── Build libwhisper.a ────────────────────────────────────────────
whisper: clone
	@echo "==> Building libwhisper.a..."
	cmake -S $(WHISPER_DIR) -B $(WHISPER_BUILD) \
		-DCMAKE_BUILD_TYPE=Release \
		-DBUILD_SHARED_LIBS=OFF \
		$(CMAKE_CUDA)
	cmake --build $(WHISPER_BUILD) --target whisper -- -j$$(nproc)

# ── Build Go binary ──────────────────────────────────────────────
build: check-portaudio whisper
	@echo "==> Building voice-controls..."
	@mkdir -p build
	CGO_ENABLED=1 \
	C_INCLUDE_PATH=$(INCLUDE_PATH) \
	LIBRARY_PATH=$(LIBRARY_PATH) \
	go build $(BUILD_FLAGS) -o $(BINARY) ./cmd/voice-controls

build-cuda:
	$(MAKE) build GGML_CUDA=1

build-wakeword-bridge: check-portaudio check-onnxruntime
	@echo "==> Building voice-controls-wakeword-bridge..."
	@mkdir -p build
	CGO_ENABLED=1 go build -o build/voice-controls-wakeword-bridge ./cmd/wakeword-bridge

# Build dev smoke-test binaries (not installed)
build-smoke: check-portaudio whisper
	@mkdir -p build
	CGO_ENABLED=1 \
	C_INCLUDE_PATH=$(INCLUDE_PATH) \
	LIBRARY_PATH=$(LIBRARY_PATH) \
	go build $(BUILD_FLAGS) -o build/stt-smoke ./cmd/stt-smoke
	CGO_ENABLED=1 \
	C_INCLUDE_PATH=$(INCLUDE_PATH) \
	LIBRARY_PATH=$(LIBRARY_PATH) \
	go build $(BUILD_FLAGS) -o build/audio-smoke ./cmd/audio-smoke

# Build wakeword pipeline smoke-test binary (not installed, requires onnxruntime)
build-wakeword-smoke: check-portaudio whisper check-onnxruntime
	@mkdir -p build
	CGO_ENABLED=1 \
	C_INCLUDE_PATH=$(INCLUDE_PATH) \
	LIBRARY_PATH=$(LIBRARY_PATH) \
	go build $(BUILD_FLAGS) -o build/wakeword-smoke ./cmd/wakeword-smoke

# ── Install ──────────────────────────────────────────────────────
install: build
	./scripts/install.sh

# ── Model download ────────────────────────────────────────────────
model:
	@mkdir -p $(MODELS_DIR)
	./scripts/download-model.sh $(MODELS_DIR)

# ── Smoke test (requires model) ──────────────────────────────────
smoke: build-smoke
	@test -f $(MODELS_DIR)/ggml-distil-large-v3.bin || (echo "Run 'make model' first" && exit 1)
	./build/stt-smoke \
		-model $(MODELS_DIR)/ggml-distil-large-v3.bin \
		-wav third_party/whisper.cpp/samples/jfk.wav \
		2>/dev/null

# ── Test / Lint / Format ──────────────────────────────────────────
test: check-portaudio whisper
	CGO_ENABLED=1 \
	C_INCLUDE_PATH=$(INCLUDE_PATH) \
	LIBRARY_PATH=$(LIBRARY_PATH) \
	go test $(BUILD_FLAGS) ./...

lint: check-portaudio whisper
	CGO_ENABLED=1 \
	C_INCLUDE_PATH=$(INCLUDE_PATH) \
	LIBRARY_PATH=$(LIBRARY_PATH) \
	go vet $(BUILD_FLAGS) ./...

fmt:
	go fmt ./...

# ── Clean ─────────────────────────────────────────────────────────
clean:
	rm -rf build
	rm -rf $(WHISPER_BUILD)
	go clean

clean-all: clean
	rm -rf $(WHISPER_DIR)
	rm -rf $(MODELS_DIR)
