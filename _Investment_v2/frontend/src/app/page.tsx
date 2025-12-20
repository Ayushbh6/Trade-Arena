"use client";

import Link from "next/link";
import { ArrowRight, Bot, LineChart, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="h-screen w-screen bg-black text-white flex flex-col items-center justify-center relative overflow-hidden">

      {/* Background Elements */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,_var(--tw-gradient-stops))] from-indigo-900/20 via-black to-black z-0 pointer-events-none" />
      <div className="absolute top-0 left-0 w-full h-full bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-20 z-0 pointer-events-none"></div>

      <div className="z-10 text-center max-w-2xl px-6">
        <div className="mb-6 flex justify-center">
          <div className="h-16 w-16 bg-white/10 rounded-2xl flex items-center justify-center border border-white/10 backdrop-blur-md shadow-2xl shadow-indigo-500/10">
            <Bot className="h-8 w-8 text-white" />
          </div>
        </div>

        <h1 className="text-4xl md:text-6xl font-bold tracking-tight text-transparent bg-clip-text bg-gradient-to-br from-white via-white/80 to-white/40 mb-6">
          Investment Agent <span className="font-light text-indigo-400">v2.0</span>
        </h1>

        <p className="text-lg text-white/50 mb-10 leading-relaxed">
          An autonomous, AI-driven quantitative trading system designed to analyze markets, execute strategies, and manage risk in real-time.
        </p>

        <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
          <Link href="/dashboard">
            <Button size="lg" className="h-12 px-8 bg-white text-black hover:bg-white/90 rounded-full font-medium text-base transition-all hover:scale-105">
              Launch Dashboard
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
          <div className="flex gap-6 mt-6 sm:mt-0 sm:ml-6 text-sm text-white/40">
            <div className="flex items-center gap-2">
              <LineChart className="h-4 w-4" />
              <span>Real-time Analysis</span>
            </div>
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" />
              <span>Risk Managed</span>
            </div>
          </div>
        </div>
      </div>

      <div className="absolute bottom-8 left-0 w-full text-center text-white/20 text-xs">
        Powered by Multi-Agent System Architecture
      </div>

    </main>
  );
}