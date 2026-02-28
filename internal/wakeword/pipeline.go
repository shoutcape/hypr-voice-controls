package wakeword

// pipeline runs the three-stage openWakeWord inference chain:
//
//	raw audio (float32, 16 kHz, NOT normalised) →
//	  [melspectrogram model] →
//	  scale: (x/10)+2  →
//	  [embedding model] →
//	  [wakeword classifier] →
//	  score (0–1)
//
// Audio is buffered internally; callers feed arbitrary-length chunks and the
// pipeline produces one wakeword score per completed 80 ms inference step.
//
// Audio format:  mono float32 at 16 kHz.
// Conversion:    direct cast from int16 — do NOT normalise to [-1, 1].
//
// Pipeline constants (from the openWakeWord reference implementation):
//
//	chunkSamples  = 1280       (80 ms @ 16 kHz, one inference tick)
//	melFrames     ≈ 5          (per chunk; ceil(1280/160)-3)
//	embWindowMel  = 76         (mel frames needed for one embedding)
//	embStepMel    = 8          (mel frames consumed per embedding step)
//	wwWindowEmb   = 16         (embeddings needed for one wakeword prediction)
//	melScale      = (x/10)+2   (normalises melspec output for the embedding model)
//
// ONNX model I/O shapes (matching openWakeWord's published models):
//
//	melspec:   input  [1, 1280] float32  →  output [1, 1, F, 32] float32
//	embedding: input  [1, 76, 32, 1] float32  →  output [1, 1, 1, 96] float32
//	wakeword:  input  [1, 16, 96] float32  →  output [1, 1] float32

import (
	"fmt"
	"sync"

	ort "github.com/yalue/onnxruntime_go"
)

const (
	// chunkSamples is the number of audio samples per inference tick (80 ms @ 16 kHz).
	chunkSamples = 1280

	// melBins is the number of mel frequency bins produced by the melspec model.
	melBins = 32

	// embWindowMel is the number of mel frames the embedding model requires.
	embWindowMel = 76

	// embStepMel is how many new mel frames are consumed per embedding step.
	// Matches the embedding model's stride: 8 frames = 80 ms.
	embStepMel = 8

	// wwWindowEmb is the number of embeddings the wakeword model requires.
	wwWindowEmb = 16

	// embDim is the dimensionality of each embedding vector.
	embDim = 96

	// melBufCap is the maximum mel frames to keep (~10 seconds).
	melBufCap = 970

	// embBufCap is the maximum embeddings to keep (~10 seconds).
	embBufCap = 120

	// melScale constants applied after melspec: scaled = (raw/melScaleDiv) + melScaleAdd
	melScaleDiv = 10.0
	melScaleAdd = 2.0

	// Keep wakeword inference single-threaded so always-on detection does not
	// monopolize CPU on systems with many cores.
	wakewordIntraOpThreads = 1
	wakewordInterOpThreads = 1
)

// ortOnce ensures the ONNX Runtime environment is initialized exactly once
// per process, even if multiple pipelines are created.
var (
	ortOnce sync.Once
	ortErr  error
)

// InitORT initialises the global ONNX Runtime environment.
// It is safe to call multiple times; subsequent calls are no-ops.
// Must be called before creating any pipeline.
func InitORT(libPath string) error {
	ortOnce.Do(func() {
		if libPath != "" {
			ort.SetSharedLibraryPath(libPath)
		}
		ortErr = ort.InitializeEnvironment()
	})
	return ortErr
}

// DestroyORT tears down the global ONNX Runtime environment.
// Call once at process exit, after all pipelines have been closed.
func DestroyORT() {
	_ = ort.DestroyEnvironment()
}

// pipeline holds the three ONNX sessions and the streaming buffers needed
// to run openWakeWord inference in real-time.
type pipeline struct {
	mu sync.Mutex

	// ONNX sessions (one per model stage).
	melSess *ort.AdvancedSession
	embSess *ort.AdvancedSession
	wwSess  *ort.AdvancedSession

	// Pre-allocated tensors for each stage.
	melIn  *ort.Tensor[float32] // [1, 1280]
	melOut *ort.Tensor[float32] // [1, 1, F, 32]  — F determined at load time

	embIn  *ort.Tensor[float32] // [1, 76, 32, 1]
	embOut *ort.Tensor[float32] // [1, 1, 1, 96]

	wwIn  *ort.Tensor[float32] // [1, 16, 96]
	wwOut *ort.Tensor[float32] // [1, 1]

	// Streaming state.
	audioRemainder []float32 // samples not yet forming a full chunk

	// Mel ring buffer: row-major [frame][melBins].
	melBuf    []float32 // capacity melBufCap * melBins
	melFrames int       // total mel frames written

	// Embedding ring buffer: row-major [frame][embDim].
	embBuf    []float32 // capacity embBufCap * embDim
	embFrames int       // total embedding frames written

	// Track how far the mel and embedding sliding windows have advanced.
	melStep int // next mel frame index for embedding window
	embStep int // next embedding frame index for wakeword window

	// Number of mel frames produced per chunk (derived from model output shape).
	melFramesPerChunk int
}

// newPipeline loads all three ONNX models and pre-allocates tensors.
// The caller must call Close() when done.
func newPipeline(melModelPath, embModelPath, wwModelPath string) (*pipeline, error) {
	p := &pipeline{}

	sessOpts, err := ort.NewSessionOptions()
	if err != nil {
		return nil, fmt.Errorf("session options: %w", err)
	}
	defer sessOpts.Destroy() //nolint:errcheck

	if err := sessOpts.SetIntraOpNumThreads(wakewordIntraOpThreads); err != nil {
		return nil, fmt.Errorf("set wakeword intra-op threads: %w", err)
	}
	if err := sessOpts.SetInterOpNumThreads(wakewordInterOpThreads); err != nil {
		return nil, fmt.Errorf("set wakeword inter-op threads: %w", err)
	}

	// ── Melspectrogram model ─────────────────────────────────────
	// Input:  [1, 1280] float32 audio
	// Output: [1, 1, F, 32] float32 mel frames

	// First, introspect the melspec model output shape to learn F.
	_, melOutInfos, err := ort.GetInputOutputInfo(melModelPath)
	if err != nil {
		return nil, fmt.Errorf("failed to inspect melspec model %q: %w", melModelPath, err)
	}
	// Find the output tensor F dimension.
	melF, err := melOutputFrameCount(melOutInfos, chunkSamples)
	if err != nil {
		return nil, fmt.Errorf("melspec model output shape unexpected: %w", err)
	}
	p.melFramesPerChunk = melF

	p.melIn, err = ort.NewEmptyTensor[float32](ort.NewShape(1, chunkSamples))
	if err != nil {
		return nil, fmt.Errorf("melIn tensor: %w", err)
	}
	p.melOut, err = ort.NewEmptyTensor[float32](ort.NewShape(1, 1, int64(melF), melBins))
	if err != nil {
		p.melIn.Destroy()
		return nil, fmt.Errorf("melOut tensor: %w", err)
	}

	p.melSess, err = ort.NewAdvancedSession(melModelPath,
		[]string{"input"}, []string{"output"},
		[]ort.Value{p.melIn}, []ort.Value{p.melOut},
		sessOpts,
	)
	if err != nil {
		p.melIn.Destroy()
		p.melOut.Destroy()
		return nil, fmt.Errorf("melspec session: %w", err)
	}

	// ── Embedding model ──────────────────────────────────────────
	// Input:  [1, 76, 32, 1] scaled mel frames
	// Output: [1, 1, 1, 96] embedding vector

	p.embIn, err = ort.NewEmptyTensor[float32](ort.NewShape(1, embWindowMel, melBins, 1))
	if err != nil {
		return nil, fmt.Errorf("embIn tensor: %w", err)
	}
	p.embOut, err = ort.NewEmptyTensor[float32](ort.NewShape(1, 1, 1, embDim))
	if err != nil {
		p.embIn.Destroy()
		return nil, fmt.Errorf("embOut tensor: %w", err)
	}

	p.embSess, err = ort.NewAdvancedSession(embModelPath,
		[]string{"input_1"}, []string{"conv2d_19"},
		[]ort.Value{p.embIn}, []ort.Value{p.embOut},
		sessOpts,
	)
	if err != nil {
		p.embIn.Destroy()
		p.embOut.Destroy()
		return nil, fmt.Errorf("embedding session: %w", err)
	}

	// ── Wakeword classifier model ────────────────────────────────
	// Input:  [1, 16, 96] embedding window
	// Output: [1, 1] prediction score

	// Introspect to get actual input/output names (they vary per model).
	wwInInfos, wwOutInfos, err := ort.GetInputOutputInfo(wwModelPath)
	if err != nil {
		return nil, fmt.Errorf("failed to inspect wakeword model %q: %w", wwModelPath, err)
	}
	wwInName, wwOutName, err := wwIONames(wwInInfos, wwOutInfos)
	if err != nil {
		return nil, fmt.Errorf("wakeword model I/O: %w", err)
	}

	p.wwIn, err = ort.NewEmptyTensor[float32](ort.NewShape(1, wwWindowEmb, embDim))
	if err != nil {
		return nil, fmt.Errorf("wwIn tensor: %w", err)
	}
	p.wwOut, err = ort.NewEmptyTensor[float32](ort.NewShape(1, 1))
	if err != nil {
		p.wwIn.Destroy()
		return nil, fmt.Errorf("wwOut tensor: %w", err)
	}

	p.wwSess, err = ort.NewAdvancedSession(wwModelPath,
		[]string{wwInName}, []string{wwOutName},
		[]ort.Value{p.wwIn}, []ort.Value{p.wwOut},
		sessOpts,
	)
	if err != nil {
		p.wwIn.Destroy()
		p.wwOut.Destroy()
		return nil, fmt.Errorf("wakeword session: %w", err)
	}

	// ── Allocate streaming buffers ────────────────────────────────
	p.melBuf = make([]float32, 0, melBufCap*melBins)
	p.embBuf = make([]float32, 0, embBufCap*embDim)

	return p, nil
}

// feed processes a slice of mono float32 audio samples (any length) and
// returns scores produced on this call (one per completed 80 ms tick).
// The caller is responsible for applying activation logic to the scores.
func (p *pipeline) feed(samples []float32) ([]float64, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	// Prepend leftover samples from the previous call.
	if len(p.audioRemainder) > 0 {
		samples = append(p.audioRemainder, samples...)
		p.audioRemainder = p.audioRemainder[:0]
	}

	var scores []float64
	offset := 0
	for offset+chunkSamples <= len(samples) {
		chunk := samples[offset : offset+chunkSamples]
		offset += chunkSamples

		score, err := p.processChunk(chunk)
		if err != nil {
			return scores, err
		}
		if score >= 0 {
			scores = append(scores, score)
		}
	}

	// Keep remainder for next call.
	if offset < len(samples) {
		p.audioRemainder = append(p.audioRemainder[:0], samples[offset:]...)
	}
	return scores, nil
}

// processChunk runs one 1280-sample tick through the full pipeline.
// Returns the wakeword score (0–1) when a prediction is available, or -1
// when not enough buffer history has accumulated yet for a prediction.
func (p *pipeline) processChunk(chunk []float32) (float64, error) {
	// ── Stage 1: audio → mel ─────────────────────────────────────
	copy(p.melIn.GetData(), chunk)
	if err := p.melSess.Run(); err != nil {
		return -1, fmt.Errorf("melspec inference: %w", err)
	}

	// Apply scaling: (raw/10) + 2, then append to mel buffer.
	melData := p.melOut.GetData() // [1, 1, F, 32] flattened → [F*32]
	for _, v := range melData {
		p.melBuf = append(p.melBuf, v/melScaleDiv+melScaleAdd)
	}
	p.melFrames += p.melFramesPerChunk

	// Trim mel buffer to capacity (drop oldest frames).
	if len(p.melBuf) > melBufCap*melBins {
		excess := len(p.melBuf) - melBufCap*melBins
		p.melBuf = p.melBuf[excess:]
	}

	// ── Stage 2: mel window → embedding ──────────────────────────
	// Generate new embeddings for each new step of embStepMel frames
	// that have accumulated since the last embedding.
	for p.melFrames-p.melStep >= embWindowMel+embStepMel {
		// Take the window [melStep : melStep+embWindowMel] from the buffer.
		// melBuf holds the tail of the mel stream; compute buffer-local index.
		bufStart := p.melFrames - len(p.melBuf)/melBins // oldest frame index in buf
		winStart := p.melStep - bufStart
		if winStart < 0 {
			// Window start has been evicted — skip (shouldn't happen with normal usage).
			p.melStep += embStepMel
			continue
		}
		winEnd := winStart + embWindowMel
		if winEnd*melBins > len(p.melBuf) {
			break
		}

		// Copy window into embedding input tensor: [1, 76, 32, 1].
		// embIn shape: [76 * 32] flattened (channel dim=1 is implicit).
		embInData := p.embIn.GetData()
		melSlice := p.melBuf[winStart*melBins : winEnd*melBins]
		copy(embInData, melSlice)

		if err := p.embSess.Run(); err != nil {
			return -1, fmt.Errorf("embedding inference: %w", err)
		}

		// embOut: [1, 1, 1, 96] — append the 96 floats to embedding buffer.
		p.embBuf = append(p.embBuf, p.embOut.GetData()...)
		p.embFrames++

		// Trim embedding buffer to capacity.
		if len(p.embBuf) > embBufCap*embDim {
			excess := len(p.embBuf) - embBufCap*embDim
			p.embBuf = p.embBuf[excess:]
		}

		p.melStep += embStepMel
	}

	// ── Stage 3: embedding window → wakeword score ───────────────
	if p.embFrames < wwWindowEmb {
		return -1, nil // not enough history yet
	}

	// Build wakeword input: last wwWindowEmb embeddings → [1, 16, 96].
	// We run a prediction on every new embedding frame (step=1).
	if p.embFrames <= p.embStep {
		// No new embeddings since last prediction.
		return -1, nil
	}

	// Use the most recent wwWindowEmb embeddings from the buffer.
	wwSliceStart := len(p.embBuf) - wwWindowEmb*embDim
	if wwSliceStart < 0 {
		return -1, nil
	}
	copy(p.wwIn.GetData(), p.embBuf[wwSliceStart:])

	if err := p.wwSess.Run(); err != nil {
		return -1, fmt.Errorf("wakeword inference: %w", err)
	}

	p.embStep = p.embFrames // advance step to current position
	return float64(p.wwOut.GetData()[0]), nil
}

// close destroys all ONNX sessions and tensors in the correct order.
func (p *pipeline) close() {
	p.mu.Lock()
	defer p.mu.Unlock()

	// Sessions must be destroyed before their associated tensors.
	if p.wwSess != nil {
		p.wwSess.Destroy()
	}
	if p.embSess != nil {
		p.embSess.Destroy()
	}
	if p.melSess != nil {
		p.melSess.Destroy()
	}
	if p.wwOut != nil {
		p.wwOut.Destroy()
	}
	if p.wwIn != nil {
		p.wwIn.Destroy()
	}
	if p.embOut != nil {
		p.embOut.Destroy()
	}
	if p.embIn != nil {
		p.embIn.Destroy()
	}
	if p.melOut != nil {
		p.melOut.Destroy()
	}
	if p.melIn != nil {
		p.melIn.Destroy()
	}
}

// ── Helpers ──────────────────────────────────────────────────────────────────

// melOutputFrameCount inspects the melspec model's output tensors to determine
// how many mel frames are produced per 1280-sample input chunk.
// The melspec output has shape [1, 1, F, 32]; we extract F.
func melOutputFrameCount(outputs []ort.InputOutputInfo, _ int) (int, error) {
	for _, t := range outputs {
		if len(t.Dimensions) == 4 {
			// Shape: [1, 1, F, 32] — F is at index 2.
			if t.Dimensions[3] == melBins {
				f := int(t.Dimensions[2])
				if f > 0 {
					return f, nil
				}
			}
		}
	}
	// Fallback: use empirical value (ceil(1280/160) - 3 = 5).
	return 5, nil
}

// wwIONames extracts the first input and first output tensor names from the
// wakeword model's introspection info (inputs and outputs are separate slices
// returned by GetInputOutputInfo).
func wwIONames(inputs, outputs []ort.InputOutputInfo) (inName, outName string, err error) {
	if len(inputs) == 0 {
		return "", "", fmt.Errorf("no input tensor found in wakeword model")
	}
	if len(outputs) == 0 {
		return "", "", fmt.Errorf("no output tensor found in wakeword model")
	}
	return inputs[0].Name, outputs[0].Name, nil
}
