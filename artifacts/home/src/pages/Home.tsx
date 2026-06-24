import React from 'react';
import { Activity, Server, ArrowRight, Zap, Target, BarChart2, TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/card';

export default function Home() {
  return (
    <div className="min-h-screen w-full bg-background text-foreground flex flex-col font-mono selection:bg-primary/30">
      {/* Navbar */}
      <header className="w-full border-b border-border/40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-50 fixed top-0">
        <div className="container max-w-6xl mx-auto h-16 flex items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-md bg-primary/10 flex items-center justify-center border border-primary/20">
              <Zap className="w-4 h-4 text-primary" />
            </div>
            <span className="font-bold tracking-tight">TRADING_WEBHOOK</span>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-primary/10 border border-primary/20">
              <div className="w-2 h-2 rounded-full bg-primary animate-pulse shadow-[0_0_8px_rgba(var(--primary),0.8)]" />
              <span className="text-xs font-medium text-primary">ENGINE ONLINE</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 container max-w-6xl mx-auto px-4 pt-32 pb-24 flex flex-col gap-16">
        
        {/* Hero Section */}
        <section className="flex flex-col md:flex-row gap-12 items-start justify-between animate-in fade-in slide-in-from-bottom-8 duration-1000">
          <div className="flex flex-col gap-6 max-w-2xl">
            <h1 className="text-4xl md:text-6xl font-black tracking-tighter leading-tight">
              Automated Setup <br />
              <span className="text-muted-foreground">Scoring & Execution</span>
            </h1>
            <p className="text-lg md:text-xl text-muted-foreground leading-relaxed max-w-xl font-sans">
              The central nervous system for your futures trading. Listening for TradingView alerts 24/7, scoring setups in real-time, and dispatching trade cards to Discord.
            </p>
            
            <div className="flex items-center gap-4 mt-4">
              <a 
                href="/api/dashboard"
                className="inline-flex h-12 items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-8 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
              >
                Open Live Dashboard
                <ArrowRight className="w-4 h-4" />
              </a>
            </div>
          </div>

          {/* Status Panel */}
          <Card className="w-full md:w-96 p-6 border-border/40 bg-card/50 backdrop-blur shadow-2xl flex flex-col gap-6">
            <div className="flex items-center justify-between border-b border-border/40 pb-4">
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-muted-foreground" />
                <span className="text-sm text-muted-foreground uppercase tracking-wider">System Status</span>
              </div>
              <span className="text-xs font-mono text-primary">v2.4.1</span>
            </div>

            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">Latency</span>
                <span className="text-sm font-mono">14ms</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">Uptime</span>
                <span className="text-sm font-mono">99.99%</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">Active Alerts</span>
                <span className="text-sm font-mono">2</span>
              </div>
            </div>

            <div className="rounded-md bg-muted/50 p-4 border border-border/50">
              <div className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">Webhook Endpoint</div>
              <code className="text-xs text-primary break-all">
                https://{typeof window !== 'undefined' ? window.location.hostname : 'domain.com'}/api/webhook
              </code>
            </div>
          </Card>
        </section>

        {/* Instruments Grid */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-12 duration-1000 delay-150 fill-mode-both">
          <Card className="p-6 border-border/40 bg-card hover:border-primary/50 transition-colors flex flex-col gap-4">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center mb-2">
              <Target className="w-5 h-5 text-primary" />
            </div>
            <h3 className="text-xl font-bold">MGC</h3>
            <p className="text-sm text-muted-foreground font-sans">
              Micro Gold Futures. Precision tracking of precious metal setups with custom liquidity sweep scoring.
            </p>
          </Card>

          <Card className="p-6 border-border/40 bg-card hover:border-primary/50 transition-colors flex flex-col gap-4">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center mb-2">
              <BarChart2 className="w-5 h-5 text-primary" />
            </div>
            <h3 className="text-xl font-bold">MNQ</h3>
            <p className="text-sm text-muted-foreground font-sans">
              Micro Nasdaq Futures. High-velocity index tracking focusing on volume imbalances and momentum.
            </p>
          </Card>

          <Card className="p-6 border-border/40 bg-card hover:border-primary/50 transition-colors flex flex-col gap-4">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center mb-2">
              <Activity className="w-5 h-5 text-primary" />
            </div>
            <h3 className="text-xl font-bold">MES</h3>
            <p className="text-sm text-muted-foreground font-sans">
              Micro S&amp;P 500 Futures. Broad-market index setups tracked with the same liquidity and volume scoring.
            </p>
          </Card>

          <Card className="p-6 border-border/40 bg-card hover:border-primary/50 transition-colors flex flex-col gap-4">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center mb-2">
              <TrendingUp className="w-5 h-5 text-primary" />
            </div>
            <h3 className="text-xl font-bold">MYM</h3>
            <p className="text-sm text-muted-foreground font-sans">
              Micro Dow Futures. Blue-chip index momentum with structure-aware entries and dynamic stops.
            </p>
          </Card>

          <Card className="p-6 border-border/40 bg-card hover:border-primary/50 transition-colors flex flex-col gap-4">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center mb-2">
              <Server className="w-5 h-5 text-primary" />
            </div>
            <h3 className="text-xl font-bold">Discord Integration</h3>
            <p className="text-sm text-muted-foreground font-sans">
              Clean, structured trade cards posted directly to your private server the moment criteria align.
            </p>
          </Card>
        </section>

      </main>

      {/* Footer */}
      <footer className="border-t border-border/40 py-8 mt-auto">
        <div className="container max-w-6xl mx-auto px-4 flex flex-col md:flex-row items-center justify-between gap-4 text-xs text-muted-foreground">
          <p>Trading Webhook &copy; {new Date().getFullYear()} — Private System</p>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-primary opacity-50" />
            System nominal
          </div>
        </div>
      </footer>
    </div>
  );
}