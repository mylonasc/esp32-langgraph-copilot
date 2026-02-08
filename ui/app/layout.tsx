import type { Metadata } from "next";
import "@copilotkit/react-ui/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "ESP32 Agent Console",
  description: "CopilotKit UI for a LangGraph ESP32 MCP agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
