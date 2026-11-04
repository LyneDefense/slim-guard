import { useOutletContext } from "react-router-dom";
import type { UserDetail } from "../../types";

/** Public outlet contract shared by user pages and the user's call workspace. */
export function useUser(): UserDetail {
  return useOutletContext<{ user: UserDetail }>().user;
}
