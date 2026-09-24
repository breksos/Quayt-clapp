export type Tenant = { id: string; name: string };

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
};

export type GuiRequest =
  | { cmd: "state" | "connect" | "login" | "cancelLogin" | "logout" }
  | { cmd: "configureService"; serviceUrl: string }
  | { cmd: "selectTenant"; tenantId: string };
