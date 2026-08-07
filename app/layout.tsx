import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Astronaut Photography Identification Review Portal",
  description: "Collaborative identification and review of NASA astronaut photography",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
