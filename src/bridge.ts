import { cmd, onState } from "@clappkit";
import type { Snapshot } from "./types";
export function readStatus(): Promise<Snapshot> { return cmd<Snapshot>({ cmd: "state" }); }
export function subscribeToStatus(handler: (snapshot: Snapshot) => void): Promise<() => void> { return onState<Snapshot>(handler); }
