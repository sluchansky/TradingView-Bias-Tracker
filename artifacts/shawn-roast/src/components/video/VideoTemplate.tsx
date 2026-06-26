import { motion, AnimatePresence } from 'framer-motion';
import { useVideoPlayer } from '@/lib/video';
import { Scene1 } from './video_scenes/Scene1';
import { Scene2 } from './video_scenes/Scene2';
import { Scene3 } from './video_scenes/Scene3';
import { Scene4 } from './video_scenes/Scene4';
import { Scene5 } from './video_scenes/Scene5';
import shawnClip from "@assets/ScreenRecording_06-25-2026_16-15-38_1_1782441210997.MP4";

const SCENE_DURATIONS = {
  intro: 3000,
  liveFeed: 4000,
  roast: 5500,
  enhance: 4000,
  payoff: 5000,
};

export default function VideoTemplate() {
  const { currentScene } = useVideoPlayer({ durations: SCENE_DURATIONS });

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
          opacity: currentScene === 0 ? 0 : 1,
          scale: currentScene === 0 ? 0 : currentScene === 3 ? 1.5 : 1,
          left: currentScene === 0 ? '50%' : currentScene === 1 ? '50%' : currentScene === 2 ? '25%' : currentScene === 3 ? '50%' : '50%',
          top: currentScene === 0 ? '50%' : currentScene === 1 ? '45%' : currentScene === 2 ? '45%' : currentScene === 3 ? '55%' : '45%',
          x: '-50%',
          y: '-50%',
          width: currentScene >= 1 && currentScene <= 2 ? '25vw' : currentScene === 3 ? '35vw' : '30vw',
          height: currentScene >= 1 && currentScene <= 2 ? '65vh' : currentScene === 3 ? '85vh' : '75vh',
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
        {currentScene > 0 && currentScene < 4 && (
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
        animate={{ opacity: currentScene > 0 ? 1 : 0 }}
        transition={{ duration: 0.5 }}
      />

      {/* Scenes */}
      <AnimatePresence mode="popLayout">
        {currentScene === 0 && <Scene1 key="intro" />}
        {currentScene === 1 && <Scene2 key="liveFeed" />}
        {currentScene === 2 && <Scene3 key="roast" />}
        {currentScene === 3 && <Scene4 key="enhance" />}
        {currentScene === 4 && <Scene5 key="payoff" />}
      </AnimatePresence>
    </div>
  );
}