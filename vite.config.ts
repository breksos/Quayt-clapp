import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
export default defineConfig({plugins:[react()],clearScreen:false,server:{host:"127.0.0.1",port:1420,strictPort:true},resolve:{alias:{"@clappkit":fileURLToPath(new URL("./clappkit/web/index.ts",import.meta.url))}}});
