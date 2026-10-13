import PostalMime from "postal-mime";
import { createInboxWorker } from "./inbox.js";

export default createInboxWorker((raw) => PostalMime.parse(raw));
