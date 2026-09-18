import { useEffect, useMemo } from "react";
import { PROVIDERS } from "@/lib/harness";
import { isWindows } from "@/lib/platform";
import { claudeWslUnavailableReason, useHarnessHealthStore } from "@/stores/harnessHealthStore";

/** Only Claude's Windows transport needs this availability gate. The native
 * bridges keep their existing model discovery and send-time error behavior. */
export function useHarnessProviders() {
  const health = useHarnessHealthStore((state) => state.health);
  const error = useHarnessHealthStore((state) => state.error);
  const refresh = useHarnessHealthStore((state) => state.refresh);

  useEffect(() => {
    if (!isWindows) return;
    const check = () => { void refresh(); };
    check();
    window.addEventListener("focus", check);
    return () => window.removeEventListener("focus", check);
  }, [refresh]);

  return useMemo(() => PROVIDERS.map((provider) => ({
    ...provider,
    unavailableReason: isWindows && provider.id === "claude"
      ? claudeWslUnavailableReason(health, error)
      : undefined,
  })), [health, error]);
}
