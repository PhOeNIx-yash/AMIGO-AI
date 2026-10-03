import type { Variants, Transition } from "motion/react";

export const scaleFade: Variants = {
  hidden: { opacity: 0, scale: 0.95 },
  show: { opacity: 1, scale: 1, transition: { duration: 0.2 } },
  exit: { opacity: 0, scale: 0.95, transition: { duration: 0.15 } },
};

export const staggerContainer: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.05 } },
};

export const staggerItem: Variants = {
  hidden: { opacity: 0, x: -8 },
  show: { opacity: 1, x: 0, transition: { duration: 0.15, ease: "easeOut" } },
};

export const popIn: Variants = {
  hidden: { opacity: 0, y: -6, scale: 0.96 },
  show: { opacity: 1, y: 0, scale: 1, transition: { type: "spring", stiffness: 500, damping: 30 } },
  exit: { opacity: 0, y: -6, scale: 0.96, transition: { duration: 0.15 } },
};

export const fluidSpring: Transition = { type: "spring", stiffness: 300, damping: 30 };
export const popSpring: Transition = { type: "spring", stiffness: 500, damping: 30 };
export const getTransition = (transition: Transition) => transition;