"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { getSipTrunkStatusApiV1TelephonyAriSipTrunksConfigIdStatusGet } from "@/client/sdk.gen";
import type { SipTrunkStatus } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export function ManagedSipStatusCard({ configId }: { configId: number }) {
  const { user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState<SipTrunkStatus>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const refresh = useCallback(async () => {
    if (authLoading || !user) return;
    setLoading(true);
    try {
      const result = await getSipTrunkStatusApiV1TelephonyAriSipTrunksConfigIdStatusGet({ path: { config_id: configId } });
      if (result.error) throw new Error(detailFromError(result.error, "Failed to load SIP status"));
      setStatus(result.data);
      setError("");
    } catch (e) {
      setStatus(undefined);
      setError(e instanceof Error ? e.message : "Failed to load SIP status");
    } finally { setLoading(false); }
  }, [configId, authLoading, user]);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, [refresh]);
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <CardTitle>SIP trunk connection</CardTitle>
          <CardDescription>Incoming and outgoing calls use this carrier account.</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading || authLoading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {error ? <p role="alert" className="text-destructive">{error}</p> : <>
          <Badge variant={status?.status === "registered" ? "default" : "outline"}>{status?.status ?? "Checking..."}</Badge>
          <p>{status?.message}</p>
          {status?.contact_user && <p className="text-muted-foreground">Inbound contact user: <code>{status.contact_user}</code></p>}
        </>}
        <p className="text-muted-foreground">Add your carrier numbers below. Set an inbound workflow for incoming calls and a default caller ID for outgoing calls. Each carrier can have its own configuration.</p>
      </CardContent>
    </Card>
  );
}
