import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Boundary from "./Boundary";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* A React error blanks the screen with no message, on a phone in another
        house, with the console somewhere nobody will open it. This turns that
        into a sentence on screen and a stack trace in the server log. */}
    <Boundary what="app">
      <App />
    </Boundary>
  </StrictMode>,
);
