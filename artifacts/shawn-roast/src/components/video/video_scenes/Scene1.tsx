import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

export function Scene1() {
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    const timers = [
      setTimeout(() => setPhase(1), 200),
      setTimeout(() => setPhase(2), 600),
      setTimeout(() => setPhase(3), 1200),
    ];
    return () => timers.forEach(t => clearTimeout(t));
  }, []);

  return (
    <motion.div 
      className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-bg-dark"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ scale: 2, opacity: 0, filter: "blur(20px)" }}
      transition={{ duration: 0.8, ease: "easeInOut" }}
    >
      <div className="relative text-center overflow-hidden">
        {/* Animated Globe/Globe Rings */}
        <motion.div 
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[80vw] h-[80vw] border-[40px] border-primary/20 rounded-full"
          animate={{ rotate: 360, scale: [1, 1.1, 1] }}
          transition={{ rotate: { duration: 10, repeat: Infinity, ease: "linear" }, scale: { duration: 2, repeat: Infinity, ease: "easeInOut" } }}
        />
        <motion.div 
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[60vw] h-[60vw] border-[20px] border-accent/20 rounded-full border-dashed"
          animate={{ rotate: -360 }}
          transition={{ duration: 15, repeat: Infinity, ease: "linear" }}
        />

        <div className="relative z-10 px-8 py-4 bg-primary inline-block mb-4 shadow-[0_0_50px_rgba(230,0,0,0.8)] border-4 border-white">
          <motion.h1 
            className="text-[10vw] font-display text-white tracking-widest leading-none m-0 uppercase"
            initial={{ scale: 3, opacity: 0, y: 100 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 200, damping: 15 }}
          >
            BREAKING
          </motion.h1>
        </div>
        
        <br />
        
        <div className="relative z-10 px-8 py-4 bg-white inline-block shadow-[0_0_30px_rgba(255,255,255,0.5)] border-4 border-primary">
          <motion.h2 
            className="text-[8vw] font-display text-primary tracking-widest leading-none m-0 uppercase"
            initial={{ scale: 3, opacity: 0, y: -100 }}
            animate={phase >= 1 ? { scale: 1, opacity: 1, y: 0 } : { scale: 3, opacity: 0, y: -100 }}
            transition={{ type: "spring", stiffness: 200, damping: 15 }}
          >
            NEWS
          </motion.h2>
        </div>
      </div>

      <motion.div 
        className="absolute bottom-20 left-0 w-full bg-accent text-bg-dark font-display text-4xl py-3 overflow-hidden flex whitespace-nowrap"
        initial={{ opacity: 0, y: 50 }}
        animate={phase >= 2 ? { opacity: 1, y: 0 } : { opacity: 0, y: 50 }}
      >
        <div className="animate-ticker">
          SPECIAL REPORT • SPECIAL REPORT • SPECIAL REPORT • SPECIAL REPORT • SPECIAL REPORT • SPECIAL REPORT • SPECIAL REPORT • SPECIAL REPORT • 
        </div>
      </motion.div>
    </motion.div>
  );
}