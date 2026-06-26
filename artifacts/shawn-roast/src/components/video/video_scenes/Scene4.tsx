import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

export function Scene4() {
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    const timers = [
      setTimeout(() => setPhase(1), 500),
      setTimeout(() => setPhase(2), 1200),
      setTimeout(() => setPhase(3), 2000),
      setTimeout(() => setPhase(4), 2800),
    ];
    return () => timers.forEach(t => clearTimeout(t));
  }, []);

  return (
    <motion.div 
      className="absolute inset-0 z-20 pointer-events-none"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, scale: 1.2 }}
      transition={{ duration: 0.5 }}
    >
      {/* Reticle / Enhance overlay */}
      <div className="absolute inset-0 flex items-center justify-center">
        {/* Center reticle */}
        <motion.div 
          className="w-[40vw] h-[40vw] border-2 border-primary/50 relative"
          initial={{ scale: 2, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 0.8, ease: "circOut" }}
        >
          {/* Corner brackets */}
          <div className="absolute top-0 left-0 w-8 h-8 border-t-4 border-l-4 border-primary" />
          <div className="absolute top-0 right-0 w-8 h-8 border-t-4 border-r-4 border-primary" />
          <div className="absolute bottom-0 left-0 w-8 h-8 border-b-4 border-l-4 border-primary" />
          <div className="absolute bottom-0 right-0 w-8 h-8 border-b-4 border-r-4 border-primary" />
          
          {/* Crosshairs */}
          <div className="absolute top-1/2 left-1/2 w-full h-[1px] bg-primary/30 -translate-x-1/2 -translate-y-1/2" />
          <div className="absolute top-1/2 left-1/2 w-[1px] h-full bg-primary/30 -translate-x-1/2 -translate-y-1/2" />
        </motion.div>
      </div>

      {/* "ENHANCE" Text Blinking */}
      <motion.div 
        className="absolute top-[20vh] left-[20vw] bg-black/80 text-primary font-mono text-3xl px-4 py-2 border border-primary animate-flash"
      >
        [ ENHANCING IMAGE ]
      </motion.div>

      {/* Facial Analysis UI */}
      <div className="absolute top-[30vh] right-[15vw] flex flex-col gap-4">
        {phase >= 1 && (
          <motion.div 
            className="bg-black/80 border border-accent text-white p-4 font-mono w-[20vw]"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
          >
            <span className="text-accent">TARGET:</span> SHAWN
          </motion.div>
        )}
        
        {phase >= 2 && (
          <motion.div 
            className="bg-black/80 border border-accent text-white p-4 font-mono w-[20vw]"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
          >
            <span className="text-accent">EXPRESSION:</span> MAXIMUM DRAMA
          </motion.div>
        )}

        {phase >= 3 && (
          <motion.div 
            className="bg-black/80 border border-primary text-white p-4 font-mono w-[20vw]"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
          >
            <span className="text-primary animate-pulse">WARNING:</span> INTENSE EYE CONTACT DETECTED
          </motion.div>
        )}
      </div>

    </motion.div>
  );
}