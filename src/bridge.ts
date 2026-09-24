import { cmd, onState } from "@clappkit";
import type { GuiRequest, Snapshot } from "./types";

export function runCommand(request: GuiRequest): Promise<Snapshot> {
  return cmd<Snapshot, GuiRequest>(request);
}

export function subscribeToStatus(handler: (snapshot: Snapshot) => void): Promise<() => void> {
  return onState<Snapshot>(handler);
}
