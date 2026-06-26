import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

export function Scene2() {
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    const timers = [
      setTimeout(() => setPhase(1), 800),
      setTimeout(() => setPhase(2), 1600),
    ];
    return () => timers.forEach(t => clearTimeout(t));
  }, []);

  return (
    <motion.div 
      className="absolute inset-0 z-20 pointer-events-none"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.5 }}
    >
      {/* Lower Third - Reporter Intro */}
      <motion.div 
        className="absolute bottom-[15vh] left-[10vw] w-[80vw]"
        initial={{ x: '-100vw' }}
        animate={{ x: 0 }}
        exit={{ x: '-100vw' }}
        transition={{ type: "spring", stiffness: 100, damping: 20 }}
      >
        <div className="flex">
          <div className="bg-primary text-white font-display text-4xl px-6 py-4 border-l-8 border-accent">
            LOCAL MAN
          </div>
        </div>
        <div className="bg-white p-6 shadow-2xl relative overflow-hidden">
          <motion.div 
            className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-primary to-transparent"
            animate={{ x: ['-100%', '100%'] }}
            transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
          />
          <h2 className="text-6xl font-display text-bg-dark leading-none tracking-tight">
            SHAWN
          </h2>
          <motion.p 
            className="text-2xl font-body text-bg-muted mt-2 font-bold uppercase tracking-wider"
            initial={{ opacity: 0 }}
            animate={phase >= 1 ? { opacity: 1 } : { opacity: 0 }}
          >
            Has thoughts on the situation
          </motion.p>
        </div>
      </motion.div>
    </motion.div>
  );
}