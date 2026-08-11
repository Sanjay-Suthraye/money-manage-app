import { h } from "preact";
import htm from "htm";

export const html = htm.bind(h);
export { render, Fragment } from "preact";
export { useState, useEffect, useMemo, useRef, useCallback } from "preact/hooks";
