//! Utterance segmentation; same state machine as `listener.UtteranceSegmenter`.

use std::collections::VecDeque;

pub const SAMPLE_RATE: usize = 16_000;
pub const FRAME: usize = 480;
const PRE_ROLL_FRAMES: usize = 10;
const MIN_SPEECH_SECONDS: f64 = 0.35;
const MAX_UTTERANCE_SECONDS: usize = 60;

pub enum Event {
    Start,
    End(Option<Vec<f32>>),
}

pub struct Segmenter {
    silence_frames: usize,
    min_threshold: f64,
    pub noise_floor: f64,
    pre_roll: VecDeque<Vec<f32>>,
    frames: Vec<Vec<f32>>,
    pub in_speech: bool,
    silent_run: usize,
    voiced: usize,
}

pub fn rms(frame: &[f32]) -> f64 {
    if frame.is_empty() {
        return 0.0;
    }
    (frame.iter().map(|&x| (x as f64) * (x as f64)).sum::<f64>() / frame.len() as f64).sqrt()
}

impl Segmenter {
    pub fn new(silence_seconds: f64, threshold: f64) -> Self {
        Segmenter {
            silence_frames: (silence_seconds * SAMPLE_RATE as f64 / FRAME as f64) as usize,
            min_threshold: threshold,
            noise_floor: threshold / 3.0,
            pre_roll: VecDeque::with_capacity(PRE_ROLL_FRAMES),
            frames: Vec::new(),
            in_speech: false,
            silent_run: 0,
            voiced: 0,
        }
    }

    pub fn threshold(&self) -> f64 {
        self.min_threshold.max(self.noise_floor * 3.0)
    }

    pub fn current_audio(&self) -> Vec<f32> {
        self.frames.concat()
    }

    pub fn feed(&mut self, frame: &[f32]) -> Option<Event> {
        let level = rms(frame);
        let loud = level > self.threshold();
        if !self.in_speech {
            if !loud {
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * level;
                if self.pre_roll.len() == PRE_ROLL_FRAMES {
                    self.pre_roll.pop_front();
                }
                self.pre_roll.push_back(frame.to_vec());
                return None;
            }
            self.in_speech = true;
            self.frames = self.pre_roll.drain(..).collect();
            self.frames.push(frame.to_vec());
            self.silent_run = 0;
            self.voiced = 1;
            return Some(Event::Start);
        }
        self.frames.push(frame.to_vec());
        if loud {
            self.silent_run = 0;
            self.voiced += 1;
        } else {
            self.silent_run += 1;
        }
        let too_long = self.frames.len() * FRAME >= MAX_UTTERANCE_SECONDS * SAMPLE_RATE;
        if self.silent_run >= self.silence_frames || too_long {
            let audio = self.current_audio();
            let enough = (self.voiced * FRAME) as f64 >= MIN_SPEECH_SECONDS * SAMPLE_RATE as f64;
            self.in_speech = false;
            self.frames.clear();
            return Some(Event::End(if enough { Some(audio) } else { None }));
        }
        None
    }
}
