import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

export function Scene5() {
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    const timers = [
      setTimeout(() => setPhase(1), 600),
      setTimeout(() => setPhase(2), 2000),
      setTimeout(() => setPhase(3), 3500),
    ];
    return () => timers.forEach(t => clearTimeout(t));
  }, []);

  return (
    <motion.div 
      className="absolute inset-0 z-30 bg-bg-dark flex flex-col items-center justify-center p-12"
      initial={{ opacity: 0, y: '100%' }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ type: "spring", stiffness: 80, damping: 20 }}
    >
      {/* Background Graphic */}
      <motion.div 
        className="absolute inset-0 opacity-10"
        animate={{ scale: [1, 1.1, 1], rotate: [0, 5, 0] }}
        transition={{ duration: 10, repeat: Infinity, ease: "easeInOut" }}
      >
        <div className="w-full h-full bg-[radial-gradient(circle_at_center,var(--color-primary)_0%,transparent_70%)]" />
      </motion.div>

      {/* Quote Container */}
      <div className="relative z-10 max-w-5xl text-center">
        <motion.div 
          className="text-8xl text-primary font-display leading-none mb-4 opacity-50 absolute -top-12 -left-12"
          initial={{ scale: 0, rotate: -45 }}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ type: "spring" }}
        >
          "
        </motion.div>
        
        <motion.h1 
          className="text-[6vw] font-display text-white uppercase leading-[0.9] tracking-tighter"
        >
          <motion.span 
            className="block"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            THE BIGGEST <span className="text-accent line-through opacity-50">FIRE</span>
          </motion.span>
          <motion.span 
            className="block text-primary mt-2"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={phase >= 1 ? { opacity: 1, scale: 1 } : { opacity: 0, scale: 0.8 }}
            transition={{ type: "spring", bounce: 0.5 }}
          >
            B.S. ARTIST
          </motion.span>
          <motion.span 
            className="block"
            initial={{ opacity: 0, y: -20 }}
            animate={phase >= 2 ? { opacity: 1, y: 0 } : { opacity: 0, y: -20 }}
            transition={{ duration: 0.5 }}
          >
            I'VE EVER SEEN IN MY LIFE
          </motion.span>
        </motion.h1>

        <motion.div 
          className="mt-12 inline-block border-t-4 border-b-4 border-white py-4"
          initial={{ opacity: 0, width: 0 }}
          animate={phase >= 3 ? { opacity: 1, width: "100%" } : { opacity: 0, width: 0 }}
          transition={{ duration: 0.8 }}
        >
          <p className="text-3xl font-body text-white font-bold tracking-[0.2em] uppercase">
            — EVERYONE WHO KNOWS SHAWN
          </p>
        </motion.div>
      </div>

      <motion.div 
        className="absolute bottom-8 right-12 opacity-50 font-mono text-xl"
        animate={{ opacity: [0.2, 0.8, 0.2] }}
        transition={{ duration: 2, repeat: Infinity }}
      >
        [ END TRANSMISSION ]
      </motion.div>
    </motion.div>
  );
}