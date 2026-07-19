import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";

export function useConsoleQuery<T>(loader: () => Promise<T>, dependencies: readonly unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<number | null>(null);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const requestGeneration = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const response = await loader();
      if (generation.current === requestGeneration) setData(response);
    } catch (reason) {
      if (generation.current !== requestGeneration) return;
      if (reason instanceof ApiError && reason.status === 401) return;
      setError(reason instanceof ApiError ? reason.status : 0);
    } finally {
      if (generation.current === requestGeneration) setLoading(false);
    }
  }, dependencies);

  useEffect(() => {
    void load();
    return () => { generation.current += 1; };
  }, [load]);
  return { data, loading, error, reload: load };
}
