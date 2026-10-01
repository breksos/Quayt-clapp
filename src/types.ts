export type Tenant = { id: string; name: string };

export type VesselCall = {
  id: string;
  vessel_name: string;
  imo_number: string;
  eta: string;
  etd: string | null;
  berth: string | null;
  status: string;
  agent_name: string | null;
};

export type VesselCallSummary = { total: number; expected: number; arrived: number; berthed: number; departed: number; cancelled: number };

export type VesselCalls = {
  status: "idle" | "loading" | "ready" | "empty" | "error";
  summary: VesselCallSummary;
  calls: VesselCall[];
  selected: VesselCall | null;
};

export type Snapshot = {
  ok: boolean;
  rev: number;
  busy: boolean;
  connection: {
    status: "notConfigured" | "checking" | "ready" | "unavailable";
    serviceUrl: string | null;
    summary: string;
  };
  authentication: {
    status: "signedOut" | "signingIn" | "authenticated" | "logoutPending";
    summary: string;
  };
  tenants: {
    active: Tenant | null;
    available: Tenant[];
    selectionRequired: boolean;
  };
  issue: { code: string; summary: string } | null;
  vesselCalls: VesselCalls;
};

export type GuiRequest =
  | { cmd: "state" | "connect" | "login" | "cancelLogin" | "logout" }
  | { cmd: "configureService"; serviceUrl: string }
  | { cmd: "selectTenant"; tenantId: string }
  | { cmd: "loadVesselCalls"; status?: "expected" | "arrived" | "berthed" | "departed" | "cancelled"; search?: string; etaFrom?: string; etaTo?: string }
  | { cmd: "selectVesselCall"; id: string };
