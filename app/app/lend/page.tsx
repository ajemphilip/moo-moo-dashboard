"use client";

// The Lend page is now "This device" on the AI engine screen. Keep the old
// address working by sending visitors there.

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Spinner } from "@/components/Spinner";

export default function LendPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/ai/?thisdevice=1");
  }, [router]);
  return <Spinner label="Opening AI engine…" />;
}
