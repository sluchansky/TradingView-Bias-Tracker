import { useState, useEffect } from "react";
import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import Home from "@/pages/Home";
import MobileHome from "@/pages/MobileHome";
import Cockpit from "@/pages/Cockpit";
import DashboardV2 from "@/pages/DashboardV2";
import NotFound from "@/pages/not-found";

const queryClient = new QueryClient();

function useIsTabletOrPhone() {
  const [mobile, setMobile] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < 1024;
  });
  useEffect(() => {
    const check = () => setMobile(window.innerWidth < 1024);
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  return mobile;
}

function Router() {
  const isMobile = useIsTabletOrPhone();
  return (
    <Switch>
      <Route path="/" component={isMobile ? MobileHome : Home} />
      <Route path="/mobile" component={MobileHome} />
      <Route path="/cockpit" component={Cockpit} />
      <Route path="/dashboard-v2" component={DashboardV2} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
