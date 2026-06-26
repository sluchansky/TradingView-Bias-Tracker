import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

const ROASTS = [
  "BREAKING: SHAWN STILL THINKS CARGO SHORTS ARE MAKING A COMEBACK",
  "UPDATE: SHAWN'S BEARD NOW OFFICIALLY CLASSIFIED AS A FIRE HAZARD",
  "LOCAL MAN SHAWN CONFUSED BY BASIC TECHNOLOGY, AGAIN",
  "INVESTIGATION: DOES SHAWN KNOW HOW LOUD HE BREATHES? EXPERTS SAY NO",
  "ALERT: SHAWN ATTEMPTED TO FIX HIS OWN PLUMBING. DISASTER ENSUES."
];

export function Scene3() {
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    const timers = [
      setTimeout(() => setPhase(1), 500),
      setTimeout(() => setPhase(2), 2000),
      setTimeout(() => setPhase(3), 3500),
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
      {/* Side-by-side Layout Panel */}
      <motion.div 
        className="absolute top-[10vh] right-[5vw] w-[40vw] flex flex-col gap-6"
        initial={{ x: '100vw' }}
        animate={{ x: 0 }}
        exit={{ opacity: 0, scale: 0.8 }}
        transition={{ type: "spring", stiffness: 80, damping: 15 }}
      >
        <div className="bg-primary text-white p-6 shadow-xl border-t-4 border-accent transform rotate-2">
          <h3 className="font-display text-5xl uppercase tracking-tighter mb-2">Eyewitness</h3>
          <p className="font-body text-xl opacity-90 uppercase">Shawn is "literally right there"</p>
        </div>

        <motion.div 
          className="bg-bg-muted/90 backdrop-blur-md p-6 border-l-4 border-primary text-white -rotate-1"
          initial={{ opacity: 0, x: 50 }}
          animate={phase >= 1 ? { opacity: 1, x: 0 } : { opacity: 0, x: 50 }}
          transition={{ type: "spring", stiffness: 100 }}
        >
          <div className="text-accent font-display text-2xl mb-1">FACT CHECK</div>
          <p className="text-2xl font-body font-bold uppercase leading-tight">
            SHAWN IS NOT A FIRE EXPERT
          </p>
        </motion.div>

        <motion.div 
          className="bg-bg-muted/90 backdrop-blur-md p-6 border-l-4 border-primary text-white rotate-2"
          initial={{ opacity: 0, x: 50 }}
          animate={phase >= 2 ? { opacity: 1, x: 0 } : { opacity: 0, x: 50 }}
          transition={{ type: "spring", stiffness: 100 }}
        >
          <div className="text-accent font-display text-2xl mb-1">OBSERVATION</div>
          <p className="text-2xl font-body font-bold uppercase leading-tight">
            BEARD IS LOOKING SUSPICIOUSLY GRAY TODAY
          </p>
        </motion.div>
      </motion.div>

      {/* News Ticker Bottom */}
      <motion.div 
        className="absolute bottom-[5vh] left-0 w-full"
        initial={{ y: '100%' }}
        animate={{ y: 0 }}
        exit={{ y: '100%' }}
        transition={{ type: "spring", stiffness: 100, damping: 20 }}
      >
        <div className="flex items-stretch h-16 shadow-2xl">
          <div className="bg-primary text-white font-display text-3xl px-8 flex items-center shrink-0 z-10">
            SHAWN WATCH
          </div>
          <div className="bg-white text-bg-dark font-body font-bold text-2xl overflow-hidden flex-1 flex items-center relative uppercase tracking-wide">
            <div className="animate-ticker flex whitespace-nowrap whitespace-pre">
              {ROASTS.join("  •••  ")}  •••  {ROASTS.join("  •••  ")}
            </div>
          </div>
        </div>
      </motion.div>

    </motion.div>
  );
}