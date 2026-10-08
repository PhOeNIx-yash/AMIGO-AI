import React from "react";
import { motion } from "motion/react";
import { ChevronRight, User } from "lucide-react";
import { ContactItem, ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { sfx } from "../utils/audio";

interface ContactPickerProps {
  question?: string;
  contacts: ContactItem[];
  onSelectContact: (contact: ContactItem) => void;
  isDark: boolean;
  colorTheme?: ColorTheme;
}

export const ContactPicker: React.FC<ContactPickerProps> = ({
  question = "Which contact would you like me to use?",
  contacts,
  onSelectContact,
  isDark,
  colorTheme = "violet",
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;

  return (
    <motion.div
      id="fluent-contact-picker-container"
      initial={{ opacity: 0, y: 18, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -14, scale: 0.98 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      className="w-full max-w-xl mx-auto relative px-2 sm:px-4"
    >
      {/* Title / Question */}
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.1 }}
        className="text-sm sm:text-base font-medium mb-3 text-center transition-colors"
        style={{ color: theme.accent }}
      >
        {question}
      </motion.p>

      {/* Frosted Mica Glass Card */}
      <div
        className={`relative rounded-2xl sm:rounded-3xl p-3 sm:p-4 shadow-2xl transition-colors duration-150 border transform-gpu ${
          isDark
            ? "acrylic-glass text-slate-100 border-white/10 shadow-black/50"
            : "acrylic-glass-light text-slate-900 border-black/10 shadow-slate-300/50"
        }`}
      >
        <div className="space-y-1.5" role="listbox" aria-label="Contacts list">
          {contacts.length === 0 ? (
            <div className="text-center py-6 text-xs opacity-60">
              No contacts found
            </div>
          ) : (
            contacts.map((contact) => (
              <ContactRow
                key={contact.id}
                contact={contact}
                onSelectContact={onSelectContact}
                isDark={isDark}
                theme={theme}
              />
            ))
          )}
        </div>
      </div>
    </motion.div>
  );
};

const ContactRow: React.FC<{
  contact: ContactItem;
  onSelectContact: (c: ContactItem) => void;
  isDark: boolean;
  theme: any;
}> = ({ contact, onSelectContact, isDark, theme }) => {
  const [imgError, setImgError] = React.useState(false);

  return (
    <motion.button
      type="button"
      role="option"
      aria-selected="false"
      aria-label={`Select ${contact.name}${contact.role ? `, ${contact.role}` : ""}`}
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.16, ease: "easeOut" }}
      onClick={() => {
        sfx.playClick();
        onSelectContact(contact);
      }}
      className={`w-full text-left flex items-center justify-between p-2.5 sm:p-3 rounded-xl cursor-pointer transition-colors duration-150 border focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 ${
        isDark
          ? "bg-white/5 hover:bg-white/12 border-white/5 hover:border-white/20 active:scale-[0.99] text-slate-100"
          : "bg-black/5 hover:bg-black/10 border-black/5 hover:border-black/15 active:scale-[0.99] text-slate-900"
      }`}
    >
      <div className="flex items-center space-x-3.5 min-w-0 flex-1">
        {/* Avatar with fallback color */}
        <div className="relative flex-shrink-0">
          {contact.avatarImg && !imgError ? (
            <img
              src={contact.avatarImg}
              alt={contact.name}
              onError={() => setImgError(true)}
              className={`w-10 h-10 rounded-xl object-cover ring-1 ${isDark ? "ring-white/20" : "ring-black/10"} shadow-md`}
              referrerPolicy="no-referrer"
            />
          ) : (
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-semibold text-sm shadow-md"
              style={{ backgroundColor: contact.avatarColor || theme.primary }}
            >
              <User className="w-5 h-5" />
            </div>
          )}
          {contact.online && (
            <span
              className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-emerald-500 ring-2 ${
                isDark ? "ring-slate-900" : "ring-white"
              }`}
              title="Online"
            />
          )}
        </div>

        {/* Name & Handle */}
        <div className="min-w-0 flex-1 pr-2">
          <div className="text-sm font-semibold tracking-tight truncate">
            {contact.name}
          </div>
          <div className="text-xs opacity-60 truncate">
            {contact.role ? `${contact.role} • ` : ""}
            {contact.handle}
          </div>
        </div>
      </div>

      {/* Right Chevron */}
      <div className="opacity-40 hover:opacity-100 transition-opacity pl-2 flex-shrink-0">
        <ChevronRight className="w-4 h-4" style={{ color: theme.accent }} />
      </div>
    </motion.button>
  );
};
