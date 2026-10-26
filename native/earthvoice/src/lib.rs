//! Audio callbacks never call Python or the network. Capture is stopped outside PTT.
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, Sample, SizedSample};
use opus::{Application, Bitrate, Channels, Decoder, Encoder};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::cell::Cell;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const RATE: u32 = 48_000;
const FRAME: usize = 960;
const JITTER: usize = FRAME * 3;
const MAX_PCM: usize = FRAME * 10;
const MAX_SOURCES: usize = 32;
type Encoded = (u8, Vec<u8>);
fn error(e: impl std::fmt::Display) -> PyErr {
    PyRuntimeError::new_err(e.to_string())
}

struct Source {
    decoder: Decoder,
    pcm: VecDeque<f32>,
    phase: f64,
    left: f32,
    right: f32,
    playing: bool,
    sequence: Option<u64>,
    touched: Instant,
}
impl Source {
    fn new() -> Result<Self, opus::Error> {
        Ok(Self {
            decoder: Decoder::new(RATE, Channels::Mono)?,
            pcm: VecDeque::new(),
            phase: 0.,
            left: 0.,
            right: 0.,
            playing: false,
            sequence: None,
            touched: Instant::now(),
        })
    }
    fn append(&mut self, data: &[f32]) {
        self.pcm.extend(data);
        if self.pcm.len() > MAX_PCM {
            self.pcm.drain(..self.pcm.len() - MAX_PCM);
            self.phase = 0.;
        }
        if self.pcm.len() >= JITTER {
            self.playing = true;
        }
    }
    fn sample(&mut self, step: f64) -> f32 {
        if !self.playing {
            return 0.;
        }
        if self.pcm.len() < 2 {
            self.playing = false;
            self.phase = 0.;
            return 0.;
        }
        let value = self.pcm[0] + (self.pcm[1] - self.pcm[0]) * self.phase as f32;
        self.phase += step;
        while self.phase >= 1. && !self.pcm.is_empty() {
            self.pcm.pop_front();
            self.phase -= 1.;
        }
        value
    }
}
struct Core {
    encoder: Encoder,
    mode: u8,
    lease: Instant,
    capture: Vec<f32>,
    encoded: VecDeque<Encoded>,
    sources: HashMap<u64, Source>,
    previous: f32,
    index: u64,
    next: f64,
    last_error: String,
    mic_level: f32,
    mic_at: Instant,
}
impl Core {
    fn new() -> Result<Self, opus::Error> {
        let mut encoder = Encoder::new(RATE, Channels::Mono, Application::Voip)?;
        encoder.set_bitrate(Bitrate::Bits(24_000))?;
        encoder.set_complexity(5)?;
        Ok(Self {
            encoder,
            mode: 0,
            lease: Instant::now(),
            capture: Vec::with_capacity(FRAME),
            encoded: VecDeque::new(),
            sources: HashMap::new(),
            previous: 0.,
            index: 0,
            next: 0.,
            last_error: String::new(),
            mic_level: 0.,
            mic_at: Instant::now() - Duration::from_secs(1),
        })
    }
    fn input<T>(&mut self, data: &[T], channels: usize, rate: u32)
    where
        T: SizedSample,
        f32: FromSample<T>,
    {
        if self.lease.elapsed() > Duration::from_millis(350) {
            self.mode = 0;
            self.capture.clear();
            self.encoded.clear();
        }
        if self.mode == 0 || channels == 0 {
            return;
        }
        let mut peak = 0f32;
        for frame in data.chunks_exact(channels) {
            let value = frame.iter().map(|v| f32::from_sample(*v)).sum::<f32>() / channels as f32;
            let value = if value.is_finite() {
                value.clamp(-1., 1.)
            } else {
                0.
            };
            peak = peak.max(value.abs());
            while self.next <= self.index as f64 {
                let alpha = (self.next - self.index as f64 + 1.).clamp(0., 1.) as f32;
                self.capture
                    .push(self.previous + (value - self.previous) * alpha);
                self.next += rate as f64 / RATE as f64;
                if self.capture.len() == FRAME {
                    let mut packet = [0u8; 512];
                    match self.encoder.encode_float(&self.capture, &mut packet) {
                        Ok(n) => {
                            if self.encoded.len() >= 8 {
                                self.encoded.pop_front();
                            }
                            self.encoded.push_back((self.mode, packet[..n].to_vec()));
                        }
                        Err(e) => self.last_error = e.to_string(),
                    }
                    self.capture.clear();
                }
            }
            self.previous = value;
            self.index += 1;
        }
        // This diagnostic meter changes only while PTT has opened the stream;
        // raw microphone samples remain in the encoder path.
        self.mic_level = self.mic_level.max(peak);
        self.mic_at = Instant::now();
    }
    fn output<T>(&mut self, data: &mut [T], channels: usize, rate: u32)
    where
        T: SizedSample + FromSample<f32>,
    {
        self.sources
            .retain(|_, s| s.touched.elapsed() < Duration::from_secs(2));
        for frame in data.chunks_exact_mut(channels.max(1)) {
            let (mut left, mut right) = (0f32, 0f32);
            for source in self.sources.values_mut() {
                let sample = source.sample(RATE as f64 / rate as f64);
                left += sample * source.left;
                right += sample * source.right;
            }
            for v in frame.iter_mut() {
                *v = T::from_sample(0.);
            }
            if channels == 1 {
                frame[0] = T::from_sample(((left + right) * 0.70710677).clamp(-0.98, 0.98));
            } else {
                frame[0] = T::from_sample(left.clamp(-0.98, 0.98));
                frame[1] = T::from_sample(right.clamp(-0.98, 0.98));
            }
        }
    }
    fn packet(
        &mut self,
        id: u64,
        sequence: u64,
        packet: &[u8],
        gain: f32,
        pan: f32,
    ) -> Result<(), String> {
        if packet.len() > 512 || packet.is_empty() || !gain.is_finite() || !pan.is_finite() {
            return Err("invalid audio frame".into());
        }
        if gain <= 0. {
            self.sources.remove(&id);
            return Ok(());
        }
        if !self.sources.contains_key(&id) {
            if self.sources.len() >= MAX_SOURCES {
                return Ok(());
            }
            self.sources
                .insert(id, Source::new().map_err(|e| e.to_string())?);
        }
        let source = self.sources.get_mut(&id).unwrap();
        if let Some(previous) = source.sequence {
            if sequence <= previous {
                return Ok(());
            }
            // TCP preserves order; conceal up to three missing 20ms frames.
            for _ in 0..((sequence.saturating_sub(previous) / 2).saturating_sub(1)).min(3) {
                let mut missing = [0f32; FRAME];
                let n = source
                    .decoder
                    .decode_float(&[], &mut missing, false)
                    .map_err(|e| e.to_string())?;
                source.append(&missing[..n]);
            }
        }
        let mut pcm = [0f32; FRAME];
        let n = source
            .decoder
            .decode_float(packet, &mut pcm, false)
            .map_err(|e| e.to_string())?;
        if n != FRAME {
            return Err("expected a 20ms Opus frame".into());
        }
        source.append(&pcm[..n]);
        source.sequence = Some(sequence);
        let angle = (pan.clamp(-1., 1.) + 1.) * std::f32::consts::FRAC_PI_4;
        source.left = gain.clamp(0., 2.) * angle.cos();
        source.right = gain.clamp(0., 2.) * angle.sin();
        source.touched = Instant::now();
        Ok(())
    }
}

fn choose(input: bool, name: Option<&str>) -> PyResult<cpal::Device> {
    let host = cpal::default_host();
    if let Some(name) = name.filter(|n| !n.is_empty() && *n != "default") {
        let devices = if input {
            host.input_devices()
        } else {
            host.output_devices()
        }
        .map_err(error)?;
        for device in devices {
            if device.name().ok().as_deref() == Some(name) {
                return Ok(device);
            }
        }
        return Err(error("selected audio device is unavailable"));
    }
    (if input {
        host.default_input_device()
    } else {
        host.default_output_device()
    })
    .ok_or_else(|| error("no default audio device"))
}
fn input_stream<T>(
    device: &cpal::Device,
    cfg: &cpal::StreamConfig,
    state: Arc<Mutex<Core>>,
) -> Result<cpal::Stream, cpal::BuildStreamError>
where
    T: SizedSample,
    f32: FromSample<T>,
{
    let (channels, rate) = (cfg.channels as usize, cfg.sample_rate.0);
    let errors = state.clone();
    device.build_input_stream(
        cfg,
        move |data: &[T], _| {
            if let Ok(mut s) = state.lock() {
                s.input(data, channels, rate);
            }
        },
        move |e| {
            if let Ok(mut s) = errors.lock() {
                s.last_error = e.to_string();
                s.mode = 0;
            }
        },
        None,
    )
}
fn output_stream<T>(
    device: &cpal::Device,
    cfg: &cpal::StreamConfig,
    state: Arc<Mutex<Core>>,
) -> Result<cpal::Stream, cpal::BuildStreamError>
where
    T: SizedSample + FromSample<f32>,
{
    let (channels, rate) = (cfg.channels as usize, cfg.sample_rate.0);
    let errors = state.clone();
    device.build_output_stream(
        cfg,
        move |data: &mut [T], _| {
            if let Ok(mut s) = state.lock() {
                s.output(data, channels, rate);
            }
        },
        move |e| {
            if let Ok(mut s) = errors.lock() {
                s.last_error = e.to_string();
            }
        },
        None,
    )
}

#[pyclass(unsendable)]
struct AudioEngine {
    state: Arc<Mutex<Core>>,
    input: Option<cpal::Stream>,
    output: Option<cpal::Stream>,
    active: Cell<u8>,
}
#[pymethods]
impl AudioEngine {
    #[new]
    #[pyo3(signature = (input=None, output=None))]
    fn new(input: Option<&str>, output: Option<&str>) -> PyResult<Self> {
        let state = Arc::new(Mutex::new(Core::new().map_err(error)?));
        let indev = choose(true, input)?;
        let outdev = choose(false, output)?;
        let incfg = indev.default_input_config().map_err(error)?;
        let outcfg = outdev.default_output_config().map_err(error)?;
        let input = match incfg.sample_format() {
            cpal::SampleFormat::F32 => input_stream::<f32>(&indev, &incfg.config(), state.clone()),
            cpal::SampleFormat::I16 => input_stream::<i16>(&indev, &incfg.config(), state.clone()),
            cpal::SampleFormat::U16 => input_stream::<u16>(&indev, &incfg.config(), state.clone()),
            _ => return Err(error("unsupported input sample format")),
        }
        .map_err(error)?;
        let output = match outcfg.sample_format() {
            cpal::SampleFormat::F32 => {
                output_stream::<f32>(&outdev, &outcfg.config(), state.clone())
            }
            cpal::SampleFormat::I16 => {
                output_stream::<i16>(&outdev, &outcfg.config(), state.clone())
            }
            cpal::SampleFormat::U16 => {
                output_stream::<u16>(&outdev, &outcfg.config(), state.clone())
            }
            _ => return Err(error("unsupported output sample format")),
        }
        .map_err(error)?;
        output.play().map_err(error)?;
        Ok(Self {
            state,
            input: Some(input),
            output: Some(output),
            active: Cell::new(0),
        })
    }
    fn set_ptt(&self, mode: u8) -> PyResult<()> {
        if mode > 3 {
            return Err(PyValueError::new_err("unknown voice channel"));
        }
        let input = self
            .input
            .as_ref()
            .ok_or_else(|| error("audio engine is closed"))?;
        {
            let mut s = self.state.lock().map_err(error)?;
            if s.mode == mode && self.active.get() == mode {
                s.lease = Instant::now();
                return Ok(());
            }
        }
        input.pause().map_err(error)?;
        self.active.set(0);
        {
            let mut s = self.state.lock().map_err(error)?;
            s.mode = 0;
            s.capture.clear();
            s.encoded.clear();
            s.index = 0;
            s.next = 0.;
            s.previous = 0.;
            s.encoder.reset_state().map_err(error)?;
            s.mic_level = 0.;
            s.mic_at = Instant::now() - Duration::from_secs(1);
            s.mode = mode;
            s.lease = Instant::now();
        }
        if mode != 0 {
            if let Err(e) = input.play() {
                if let Ok(mut s) = self.state.lock() {
                    s.mode = 0;
                }
                return Err(error(e));
            }
            self.active.set(mode);
        }
        Ok(())
    }
    fn take_encoded<'py>(&self, py: Python<'py>) -> PyResult<Vec<(u8, Bound<'py, PyBytes>)>> {
        let mut state = self.state.lock().map_err(error)?;
        Ok(state
            .encoded
            .drain(..)
            .map(|(mode, bytes)| (mode, PyBytes::new(py, &bytes)))
            .collect())
    }
    fn push_packet(
        &self,
        id: u64,
        sequence: u64,
        packet: &[u8],
        gain: f32,
        pan: f32,
    ) -> PyResult<()> {
        self.state
            .lock()
            .map_err(error)?
            .packet(id, sequence, packet, gain, pan)
            .map_err(error)
    }
    fn finish(&self, id: u64) -> PyResult<()> {
        if let Some(s) = self.state.lock().map_err(error)?.sources.get_mut(&id) {
            s.playing = !s.pcm.is_empty();
            s.sequence = None;
            s.decoder.reset_state().map_err(error)?;
        }
        Ok(())
    }
    fn remove(&self, id: u64) -> PyResult<()> {
        self.state.lock().map_err(error)?.sources.remove(&id);
        Ok(())
    }
    fn last_error(&self) -> PyResult<String> {
        Ok(self.state.lock().map_err(error)?.last_error.clone())
    }
    /// Peak input level and recent-capture state, without exposing PCM.
    fn microphone_level(&self) -> PyResult<(f32, bool)> {
        let mut s = self.state.lock().map_err(error)?;
        if self.active.get() == 0 || s.mic_at.elapsed() > Duration::from_millis(300) {
            s.mic_level = 0.;
            return Ok((0., false));
        }
        let value = s.mic_level;
        s.mic_level *= 0.5;
        Ok((value, true))
    }
    fn close(&mut self) {
        self.input.take();
        self.output.take();
        if let Ok(mut s) = self.state.lock() {
            s.mode = 0;
            s.sources.clear();
            s.encoded.clear();
        }
    }
}

/// Headless codec for wire interoperability tests; never opens a microphone.
#[pyclass(unsendable)]
struct OpusCodec {
    encoder: Encoder,
    decoder: Decoder,
}
#[pymethods]
impl OpusCodec {
    #[new]
    fn new() -> PyResult<Self> {
        let core = Core::new().map_err(error)?;
        Ok(Self {
            encoder: core.encoder,
            decoder: Decoder::new(RATE, Channels::Mono).map_err(error)?,
        })
    }
    fn encode<'py>(&mut self, py: Python<'py>, pcm: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
        if pcm.len() != FRAME * 4 {
            return Err(PyValueError::new_err("expected 960 float32 samples"));
        }
        let samples: Vec<f32> = pcm
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes(b.try_into().unwrap()))
            .collect();
        if !samples.iter().all(|v| v.is_finite()) {
            return Err(PyValueError::new_err("nonfinite PCM"));
        }
        let bytes = self
            .encoder
            .encode_vec_float(&samples, 512)
            .map_err(error)?;
        Ok(PyBytes::new(py, &bytes))
    }
    fn decode<'py>(&mut self, py: Python<'py>, packet: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
        if packet.len() > 512 {
            return Err(PyValueError::new_err("oversized Opus frame"));
        }
        let mut samples = [0f32; FRAME];
        let n = self
            .decoder
            .decode_float(packet, &mut samples, false)
            .map_err(error)?;
        let bytes: Vec<u8> = samples[..n].iter().flat_map(|v| v.to_le_bytes()).collect();
        Ok(PyBytes::new(py, &bytes))
    }
}
#[pyfunction]
fn devices() -> PyResult<(Vec<String>, Vec<String>)> {
    let host = cpal::default_host();
    let inputs = host
        .input_devices()
        .map_err(error)?
        .filter_map(|d| d.name().ok())
        .collect();
    let outputs = host
        .output_devices()
        .map_err(error)?
        .filter_map(|d| d.name().ok())
        .collect();
    Ok((inputs, outputs))
}
#[pymodule]
fn earthvoice(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<AudioEngine>()?;
    module.add_class::<OpusCodec>()?;
    module.add_function(wrap_pyfunction!(devices, module)?)?;
    module.add("VERSION", 1)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn silent_until_ptt_and_bounded_capture() {
        let mut core = Core::new().unwrap();
        core.input(&vec![0.1f32; FRAME * 20], 1, RATE);
        assert!(core.encoded.is_empty());
        core.mode = 1;
        core.input(&vec![0.1f32; FRAME * 20], 1, RATE);
        assert_eq!(core.encoded.len(), 8);
        assert!(core
            .encoded
            .iter()
            .all(|(mode, b)| *mode == 1 && !b.is_empty()));
        core.lease = Instant::now() - Duration::from_secs(1);
        core.input(&[0.1f32; FRAME], 1, RATE);
        assert_eq!(core.mode, 0);
        assert!(core.encoded.is_empty());
        assert!(core.capture.is_empty());
    }
    #[test]
    fn opus_spatial_mix_and_queue_bound() {
        let mut core = Core::new().unwrap();
        let tone: Vec<f32> = (0..FRAME).map(|i| (i as f32 * 0.1).sin() * 0.2).collect();
        for seq in 0..30 {
            let p = core.encoder.encode_vec_float(&tone, 512).unwrap();
            core.packet(1, seq * 2, &p, 1., -1.).unwrap();
        }
        assert!(core.sources[&1].pcm.len() <= MAX_PCM);
        let mut output = [0f32; FRAME * 2];
        core.output(&mut output, 2, RATE);
        assert!(output.iter().step_by(2).any(|s| s.abs() > 0.01));
        assert!(output.iter().skip(1).step_by(2).all(|s| s.abs() < 1e-5));
    }
    #[test]
    fn reject_invalid_frames_without_unbounded_sources() {
        let mut core = Core::new().unwrap();
        assert!(core.packet(1, 0, &[0; 513], 1., 0.).is_err());
        assert!(core.packet(1, 0, &[1], f32::NAN, 0.).is_err());
        core.mode = 1;
        core.input(&[0f32; FRAME], 1, 44_100);
        assert!(core.capture.len() < FRAME);
    }
}
