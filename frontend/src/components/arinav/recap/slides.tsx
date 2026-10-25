// Fixed Call Wrapped slide templates. The recap only fills them: numbers from `facts`, words
// from `content`. Slides without data (no routes, no floor changes, no quote) are left out.
import { motion, type Variants } from 'motion/react';
import type { ReactNode } from 'react';

import type { Recap, RecapContent, RecapCount, RecapFacts } from '../../../lib/api';
import CountUp from './CountUp';

export type Tone = 'navy' | 'blue' | 'violet' | 'orange' | 'light';

export interface Slide {
  id: string;
  tone: Tone;
  /** One line for screen readers announcing the slide. */
  label: string;
  body: ReactNode;
}

const group: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.14, delayChildren: 0.12 } },
};

const item: Variants = {
  hidden: { opacity: 0, y: 22 },
  show: { opacity: 1, y: 0, transition: { duration: 0.55, ease: [0.16, 1, 0.3, 1] } },
};

function Reveal({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <motion.div
      className={`wrapped__stack ${className}`}
      variants={group}
      initial="hidden"
      animate="show"
    >
      {children}
    </motion.div>
  );
}

function Item({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <motion.li className={className} variants={item}>
      {children}
    </motion.li>
  );
}

function Line({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <motion.div className={className} variants={item}>
      {children}
    </motion.div>
  );
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function dateRange(from: string | null, to: string | null): string {
  if (!from) return '';
  const day = new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' });
  const a = day.format(new Date(from));
  const b = to ? day.format(new Date(to)) : a;
  return a === b ? a : `${a} – ${b}`;
}

function Bars({ rows }: { rows: RecapCount[] }) {
  const top = Math.max(...rows.map((r) => r.count), 1);
  return (
    <ol className="wrapped__bars">
      {rows.map((row, i) => (
        <Item key={row.category} className={`wrapped__bar${i === 0 ? ' wrapped__bar--top' : ''}`}>
          <span className="wrapped__bar-label">
            <span className="wrapped__rank">{i + 1}</span>
            {row.label}
          </span>
          <motion.span
            className="wrapped__bar-fill"
            initial={{ scaleX: 0 }}
            animate={{ scaleX: row.count / top }}
            transition={{ duration: 0.8, delay: 0.35 + i * 0.12, ease: [0.16, 1, 0.3, 1] }}
          />
          <span className="wrapped__bar-count">{row.count}×</span>
        </Item>
      ))}
    </ol>
  );
}

function talk(facts: RecapFacts): Slide {
  const { minutes, questions, voice_questions: voice } = facts.talk;
  const short = minutes < 1;
  return {
    id: 'talk',
    tone: 'blue',
    label: `${short ? 'Under a minute' : `${minutes} minutes`} talking to AriNav, ${plural(questions, 'question')}`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">Time together</Line>
        <Line className="wrapped__big">
          {short ? (
            <CountUp value={Math.max(1, Math.round(minutes * 60))} />
          ) : (
            <CountUp value={minutes} decimals={minutes < 10 ? 1 : 0} />
          )}
        </Line>
        <Line className="wrapped__lead">{short ? 'seconds' : 'minutes'} talking to AriNav</Line>
        <Line className="wrapped__meta">
          {plural(questions, 'question')} asked
          {voice > 0 && <> · {voice} by voice</>}
        </Line>
      </Reveal>
    ),
  };
}

function searches(facts: RecapFacts): Slide | null {
  if (!facts.searches.length) return null;
  const [first] = facts.searches;
  return {
    id: 'searches',
    tone: 'orange',
    label: `You looked for ${facts.searches.map((s) => `${s.label} ${s.count} times`).join(', ')}`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">You went looking for</Line>
        <Line className="wrapped__title">
          {capital(first.label)}, {first.count === 1 ? 'once' : `${first.count} times`}
        </Line>
        <Bars rows={facts.searches} />
      </Reveal>
    ),
  };
}

function distance(facts: RecapFacts): Slide | null {
  const { count, distance_m: metres, longest, comparison } = facts.routes;
  if (!count) return null;
  return {
    id: 'distance',
    tone: 'navy',
    label: `${metres} metres of routes planned, about ${comparison.value} ${comparison.unit}`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">{plural(count, 'route')} planned</Line>
        <Line className="wrapped__big">
          <CountUp value={metres} decimals={metres < 100 ? 1 : 0} />
          <span className="wrapped__unit">m</span>
        </Line>
        <Line className="wrapped__lead">
          That's about {comparison.value} {comparison.unit}.
        </Line>
        {longest?.to && (
          <Line className="wrapped__card">
            <span className="wrapped__card-kicker">Longest route</span>
            <span>
              {longest.from ?? 'Your start'} → {longest.to}
            </span>
            <span className="wrapped__card-value">{longest.distance_m} m</span>
          </Line>
        )}
      </Reveal>
    ),
  };
}

function vertical(facts: RecapFacts): Slide | null {
  const { floor_changes: changes, climbed_m: up, descended_m: down, steps } = facts.vertical;
  const total = changes.stairs + changes.lift;
  if (!total) return null;
  const climbed = Math.round((up.stairs + up.lift) * 10) / 10;
  const descended = Math.round((down.stairs + down.lift) * 10) / 10;
  return {
    id: 'vertical',
    tone: 'violet',
    label: `${plural(total, 'floor change')}: ${changes.stairs} by stairs, ${changes.lift} by lift; ${climbed} m up, ${descended} m down`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">Up and down</Line>
        <Line className="wrapped__big">
          <CountUp value={total} />
        </Line>
        <Line className="wrapped__lead">{total === 1 ? 'floor change' : 'floor changes'}</Line>
        <Line className="wrapped__split">
          <div>
            <span className="wrapped__split-value">{changes.stairs}</span>
            <span>by stairs</span>
          </div>
          <div>
            <span className="wrapped__split-value">{changes.lift}</span>
            <span>by lift</span>
          </div>
        </Line>
        <Line className="wrapped__meta">
          {[
            climbed > 0 && `↑ ${climbed} m up`,
            descended > 0 && `↓ ${descended} m down`,
            steps > 0 && `≈ ${steps} stair steps`,
          ]
            .filter(Boolean)
            .join(' · ')}
        </Line>
      </Reveal>
    ),
  };
}

function topics(content: RecapContent): Slide {
  return {
    id: 'topics',
    tone: 'light',
    label: `Top topics: ${content.topTopics.join(', ')}`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">What you talked about</Line>
        <ol className="wrapped__topics">
          {content.topTopics.map((topic, i) => (
            <Item key={topic}>
              <span className="wrapped__rank">{i + 1}</span>
              {topic}
            </Item>
          ))}
        </ol>
      </Reveal>
    ),
  };
}

function mood(content: RecapContent): Slide {
  return {
    id: 'mood',
    tone: 'orange',
    label: `The vibe: ${content.mood} ${content.moodEmoji}`,
    body: (
      <Reveal className="wrapped__center">
        <Line className="wrapped__kicker">The vibe</Line>
        <motion.div
          className="wrapped__emoji"
          aria-hidden="true"
          initial={{ scale: 0.2, rotate: -20, opacity: 0 }}
          animate={{ scale: 1, rotate: 0, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 220, damping: 12, delay: 0.25 }}
        >
          {content.moodEmoji}
        </motion.div>
        <Line className="wrapped__title">{content.mood}</Line>
      </Reveal>
    ),
  };
}

function quote(content: RecapContent): Slide | null {
  if (!content.bestQuote) return null;
  return {
    id: 'quote',
    tone: 'navy',
    label: `Highlight: “${content.bestQuote}”`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">Highlight</Line>
        <Line className="wrapped__quote-mark">“</Line>
        <Line>
          <blockquote className="wrapped__quote">{content.bestQuote}</blockquote>
        </Line>
      </Reveal>
    ),
  };
}

function alltime(facts: RecapFacts): Slide {
  const all = facts.alltime;
  return {
    id: 'alltime',
    tone: 'blue',
    label: `All-time on AriNav: ${plural(all.conversations, 'conversation')}, ${plural(all.questions, 'question')}, ${all.distance_m} metres, ${plural(all.floor_changes, 'floor change')}`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">All-time on AriNav</Line>
        <div className="wrapped__grid">
          {[
            [all.conversations, 'conversations'],
            [all.questions, 'questions'],
            [all.distance_m, 'metres planned'],
            [all.floor_changes, 'floor changes'],
          ].map(([value, text]) => (
            <Line key={text} className="wrapped__tile">
              <span className="wrapped__tile-value">
                <CountUp value={Number(value)} delay={0.4} />
              </span>
              <span>{text}</span>
            </Line>
          ))}
        </div>
        {all.top_searches.length > 0 && (
          <Line className="wrapped__chips">
            {all.top_searches.map((s) => (
              <span key={s.category} className="wrapped__chip">
                {s.label} · {s.count}×
              </span>
            ))}
          </Line>
        )}
      </Reveal>
    ),
  };
}

function summary(content: RecapContent, actions: ReactNode): Slide {
  return {
    id: 'summary',
    tone: 'navy',
    label: `Summary: ${content.summary}`,
    body: (
      <Reveal>
        <Line className="wrapped__kicker">That's a wrap</Line>
        <Line className="wrapped__summary">{content.summary}</Line>
        {content.nextSteps.length > 0 && (
          <Line className="wrapped__card">
            <span className="wrapped__card-kicker">Next time</span>
            <ul className="wrapped__steps">
              {content.nextSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          </Line>
        )}
        <Line className="wrapped__actions">{actions}</Line>
      </Reveal>
    ),
  };
}

/** The story for a ready recap; `actions` (replay, close) go on the last slide. */
export function buildSlides(recap: Recap, actions: ReactNode): Slide[] {
  const { facts, content } = recap;
  if (!facts || !content) return [];
  const intro: Slide = {
    id: 'intro',
    tone: 'navy',
    label: `Your last ${plural(facts.conversations, 'conversation')} with AriNav`,
    body: (
      <Reveal>
        <Line className="wrapped__brand">AriNav · Call Wrapped</Line>
        <Line className="wrapped__hero">
          Your last {plural(facts.conversations, 'conversation')} with AriNav
        </Line>
        <Line className="wrapped__meta">
          {dateRange(facts.from, facts.to)}
          {facts.buildings.length > 0 && <> · {facts.buildings.join(', ')}</>}
        </Line>
      </Reveal>
    ),
  };
  return [
    intro,
    talk(facts),
    searches(facts),
    distance(facts),
    vertical(facts),
    topics(content),
    mood(content),
    quote(content),
    alltime(facts),
    summary(content, actions),
  ].filter((s): s is Slide => s !== null);
}
