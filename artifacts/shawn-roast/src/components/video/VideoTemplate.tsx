import { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useVideoPlayer } from '@/lib/video';
import { Scene1 } from './video_scenes/Scene1';
import { Scene2 } from './video_scenes/Scene2';
import { Scene3 } from './video_scenes/Scene3';
import { Scene4 } from './video_scenes/Scene4';
import { Scene5 } from './video_scenes/Scene5';
import shawnClip from "@assets/ScreenRecording_06-25-2026_16-15-38_1_1782441210997.MP4";

export const SCENE_DURATIONS = {
  intro: 3000,
  liveFeed: 4000,
  roast: 5500,
  enhance: 4000,
  payoff: 5000,
};

const SCENE_COMPONENTS: Record<string, React.ComponentType> = {
  intro: Scene1,
  liveFeed: Scene2,
  roast: Scene3,
  enhance: Scene4,
  payoff: Scene5,
};

const SCENE_START_SEC: Record<string, number> = (() => {
  const out: Record<string, number> = {};
  let cumulativeMs = 0;
  for (const [key, ms] of Object.entries(SCENE_DURATIONS)) {
    out[key] = cumulativeMs / 1000;
    cumulativeMs += ms;
  }
  return out;
})();

const AUDIO_SEEK_EPSILON_SEC = 0.18;

export default function VideoTemplate({
  durations = SCENE_DURATIONS,
  loop = true,
  muted = false,
  onSceneChange,
}: {
  durations?: Record<string, number>;
  loop?: boolean;
  muted?: boolean;
  onSceneChange?: (sceneKey: string) => void;
} = {}) {
  const { currentSceneKey } = useVideoPlayer({ durations, loop });

  useEffect(() => {
    onSceneChange?.(currentSceneKey);
  }, [currentSceneKey, onSceneChange]);

  const baseSceneKey = currentSceneKey.replace(/_r[12]$/, '') as keyof typeof SCENE_DURATIONS;
  const sceneIndex = Object.keys(SCENE_DURATIONS).indexOf(baseSceneKey);
  const SceneComponent = SCENE_COMPONENTS[baseSceneKey];

  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.volume = 0.45;
    const targetTime = SCENE_START_SEC[baseSceneKey] ?? 0;
    if (Math.abs(audio.currentTime - targetTime) > AUDIO_SEEK_EPSILON_SEC) {
      audio.currentTime = targetTime;
    }
    audio.play().catch(() => {});
  }, [currentSceneKey, baseSceneKey, muted]);

  return (
    <div className="relative w-full h-screen overflow-hidden bg-bg-dark">
      {/* Background layer - News Studio Image */}
      <div className="absolute inset-0 z-0">
        <img
          src={`${import.meta.env.BASE_URL}studio_bg.png`}
          alt="Studio Background"
          className="w-full h-full object-cover opacity-60"
        />
        <div className="absolute inset-0 bg-secondary/40 mix-blend-multiply" />
        
        {/* Animated grid/overlay for that "news" feel */}
        <div 
          className="absolute inset-0 opacity-20 pointer-events-none"
          style={{
            backgroundImage: `linear-gradient(rgba(255, 255, 255, 0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255, 255, 255, 0.1) 1px, transparent 1px)`,
            backgroundSize: '40px 40px'
          }}
        />
        
        {/* Subtle pulsing red glow */}
        <motion.div 
          className="absolute top-0 right-0 w-[80vw] h-[80vw] rounded-full blur-[150px] bg-primary/20"
          animate={{ scale: [1, 1.2, 1], opacity: [0.3, 0.6, 0.3] }}
          transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
        />
      </div>

      {/* Main Video Element - Lives here to play continuously, but masked/positioned by scenes */}
      <motion.div 
        className="absolute z-10 flex items-center justify-center overflow-hidden border-4 border-white/20 shadow-2xl bg-black"
        initial={{ opacity: 0, scale: 0, x: '50vw', y: '50vh', xPercent: -50, yPercent: -50 }}
        animate={{
          opacity: sceneIndex === 0 ? 0 : 1,
          scale: sceneIndex === 0 ? 0 : sceneIndex === 3 ? 1.5 : 1,
          left: sceneIndex === 0 ? '50%' : sceneIndex === 1 ? '50%' : sceneIndex === 2 ? '25%' : sceneIndex === 3 ? '50%' : '50%',
          top: sceneIndex === 0 ? '50%' : sceneIndex === 1 ? '45%' : sceneIndex === 2 ? '45%' : sceneIndex === 3 ? '55%' : '45%',
          x: '-50%',
          y: '-50%',
          width: sceneIndex >= 1 && sceneIndex <= 2 ? '25vw' : sceneIndex === 3 ? '35vw' : '30vw',
          height: sceneIndex >= 1 && sceneIndex <= 2 ? '65vh' : sceneIndex === 3 ? '85vh' : '75vh',
        }}
        transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
        style={{
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.75), 0 0 40px rgba(230, 0, 0, 0.3)'
        }}
      >
        <video
          src={shawnClip}
          autoPlay
          muted
          loop
          playsInline
          className="w-full h-full object-cover"
        />
        
        {/* Scanlines over video */}
        <div className="absolute inset-0 pointer-events-none opacity-20 bg-[url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjEiIGZpbGw9IiNmZmYiLz48L3N2Zz4=')] bg-repeat" />
        
        {/* "LIVE" Bug */}
        {sceneIndex > 0 && sceneIndex < 4 && (
          <div className="absolute top-4 left-4 bg-primary text-white font-display px-3 py-1 text-xl flex items-center gap-2 rounded-sm z-20">
            <motion.div 
              className="w-3 h-3 rounded-full bg-white"
              animate={{ opacity: [1, 0, 1] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
            LIVE
          </div>
        )}
      </motion.div>

      {/* Persistent Breaking News Banner (Top) */}
      <motion.div 
        className="absolute top-0 left-0 w-full h-2 z-30 bg-accent"
        animate={{ opacity: sceneIndex > 0 ? 1 : 0 }}
        transition={{ duration: 0.5 }}
      />

      {/* Scenes */}
      <AnimatePresence mode="popLayout">
        {SceneComponent && <SceneComponent key={currentSceneKey} />}
      </AnimatePresence>

      {/* Background music (scene-synced) */}
      <audio
        ref={audioRef}
        src={`${import.meta.env.BASE_URL}audio/bg_music.mp3`}
        preload="auto"
        autoPlay
        muted={muted}
      />
    </div>
  );
}
