import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "./AppShell";
import { Placeholder } from "./Placeholder";
import { DocumentPage } from "../components/document/DocumentPage";
import { ImportsPage } from "../components/imports/ImportsPage";
import { SearchPage } from "../components/search/SearchPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Placeholder /> },
      { path: "items/:itemId", element: <DocumentPage /> },
      { path: "imports", element: <ImportsPage /> },
      { path: "search", element: <SearchPage /> },
    ],
  },
]);
