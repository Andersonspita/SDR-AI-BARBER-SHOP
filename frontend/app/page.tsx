"use client";

import { AuthGate } from "@/features/auth";
import { Dashboard } from "@/features/dashboard";

/** Página única do painel SDR-AI, protegida por login. */
export default function HomePage() {
  return <AuthGate>{(session) => <Dashboard session={session} />}</AuthGate>;
}
