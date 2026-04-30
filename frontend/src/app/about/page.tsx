import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = {
  title: 'About — peerlo',
  description:
    'Where the data comes from, how we process it, what it covers and what it does not, and who built this.',
};

export default function AboutPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-12 space-y-12 animate-fade-up">
      <header className="space-y-4">
        <p className="text-xs uppercase tracking-[0.2em] text-primary font-medium">About</p>
        <h1 className="font-serif text-4xl sm:text-5xl text-foreground leading-[1.1]">
          The public record of US giving, made searchable.
        </h1>
        <p className="text-muted-foreground text-lg leading-relaxed">
          peerlo is a free tool for searching IRS Form 990 filings — the public tax
          returns that nonprofits and private foundations file each year. We built it because
          this data is technically public but practically hard to use.
        </p>
      </header>

      <Section title="What you can do here">
        <p>
          Search organizations by name, EIN, or the language of their mission and programs.
          Open an organization to see its identity, its 990 narrative, and the grants it has
          given or received as reported on Form 990 Schedule I (public charities) or Form
          990-PF Part XV (private foundations).
        </p>
        <p>
          The product is built for people who think in terms of <em>who funds whom</em> —
          philanthropic advisors, foundation program officers, catalytic capital investors,
          journalists, and nonprofit operators trying to understand their ecosystem.
        </p>
      </Section>

      <Section title="Where the data comes from">
        <p>
          All data on this site originates from IRS Form 990 e-filings — the legally required
          annual returns that tax-exempt organizations submit to the federal government. The
          IRS publishes these returns as bulk XML.
        </p>
        <p>
          We access this data through{' '}
          <ExternalLink href="https://nonprofitecosystem.givingtuesday.org/datamarts/">
            GivingTuesday&rsquo;s Data Commons
          </ExternalLink>
          , which parses the IRS XML into clean, analysis-ready datamarts and makes them
          freely available. Their work is the reason this product is possible at the scale and
          quality it is — without their parsing layer, every team building on 990 data would
          be reinventing the same wheel.
        </p>
        <p className="text-sm text-muted-foreground">
          The specific tables behind this site include 990 basic fields, 990-PF basic fields,
          990 mission and program narratives, Schedule O Part III narratives, Schedule I
          grants given by public charities, and Form 990-PF grants given by private
          foundations.
        </p>
      </Section>

      <Section title="How we process it">
        <p>
          We take GivingTuesday&rsquo;s parsed datamarts and load them into a Postgres database
          built for fast search and lineage. For each organization we resolve a single
          canonical identity from its most recent filing, attach its narrative text, and link
          it to the grants it has given and received.
        </p>
        <p>
          Search runs in two modes. <em>Name matching</em> is a substring match against the
          canonical org name and any DBAs. <em>Narrative matching</em> is a full-text search
          over the mission, program activities, and Schedule O Part III text using Postgres
          full-text indexes with English stemming. The default mode combines both signals and
          ranks name matches above narrative-only matches.
        </p>
        <p>
          Every filing we display is tagged with a source version and run ID so you can trace
          a record back to the underlying ingestion. Look for the lineage line at the bottom
          of any organization page.
        </p>
      </Section>

      <Section title="What this data does not cover">
        <p>The honest disclosures matter as much as what we have:</p>
        <ul className="list-disc pl-5 space-y-2">
          <li>
            <strong className="text-foreground/90">Paper filers are excluded.</strong> Mandatory
            e-filing applies to tax years beginning after July 2019. Smaller organizations
            with prior-year paper returns are missing or partial.
          </li>
          <li>
            <strong className="text-foreground/90">Lag is real.</strong> A 990 filed in
            calendar year 2024 may cover a 2022 or 2023 fiscal year. Treat the data as a
            historical record, not a real-time feed.
          </li>
          <li>
            <strong className="text-foreground/90">Smallest nonprofits file 990-N.</strong>{' '}
            Organizations under $50K in gross receipts file the &ldquo;e-postcard&rdquo; which
            contains almost no detail. They appear in IRS lists but not in this product&rsquo;s
            narratives or grant records.
          </li>
          <li>
            <strong className="text-foreground/90">Grant recipient EINs are messy.</strong>{' '}
            Foundations report grantees as free text. Matching that text back to a recipient
            EIN is a hard data problem and not something we currently do at scale on this
            site.
          </li>
          <li>
            <strong className="text-foreground/90">Self-reported.</strong> Form 990 narratives
            are written by the filer. They reflect how the organization describes itself, not
            an independent audit.
          </li>
        </ul>
      </Section>

      <Section title="Who built this">
        <p>
          peerlo is built and maintained by{' '}
          <ExternalLink href="https://www.vibrantdatalabs.org/">Vibrant Data Labs</ExternalLink>
          . VDL builds data infrastructure, analysis tools, and interactive products for
          climate finance, philanthropy, and the broader nonprofit sector. We work with
          foundations, catalytic capital investors, and mission-driven organizations trying to
          understand and move capital more effectively.
        </p>
        <p>
          Other things we&rsquo;ve built using related data include the{' '}
          <ExternalLink href="https://usclimate.vibrantdatalabs.org/">
            US Climate Finance Tracker
          </ExternalLink>
          .
        </p>
        <p className="text-sm text-muted-foreground pt-2 border-t border-border/40">
          Found a bug, a missing organization, or want to use this data in your own work?
          Reach out at{' '}
          <a
            href="mailto:hello@vibrantdatalabs.org"
            className="text-foreground/80 hover:text-primary transition-colors underline-offset-2 hover:underline"
          >
            hello@vibrantdatalabs.org
          </a>
          .
        </p>
      </Section>

      <div className="pt-4">
        <Link
          href="/"
          className="text-sm text-primary hover:text-primary/80 transition-colors"
        >
          ← Back to search
        </Link>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="font-serif text-2xl text-foreground">{title}</h2>
      <div className="space-y-3 text-foreground/80 leading-relaxed">{children}</div>
    </section>
  );
}

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-foreground/90 hover:text-primary transition-colors underline-offset-2 underline decoration-border hover:decoration-primary"
    >
      {children}
    </a>
  );
}
