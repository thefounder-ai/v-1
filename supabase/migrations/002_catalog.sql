-- SkillOrbit catalog: original learning modules plus curated official resources.
-- Run after 001_profiles.sql in the Supabase SQL editor.

create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title text not null,
  description text not null,
  short_summary text not null,
  content_type text not null check (
    content_type in (
      'original_module',
      'external_course',
      'documentation',
      'tutorial',
      'project',
      'learning_path'
    )
  ),
  provider text not null default 'SkillOrbit',
  source_url text,
  license_info text,
  category text not null,
  difficulty text not null check (difficulty in ('Beginner', 'Intermediate', 'Advanced')),
  duration_minutes integer not null check (duration_minutes > 0),
  skills jsonb not null default '[]'::jsonb,
  prerequisites jsonb not null default '[]'::jsonb,
  learning_outcomes jsonb not null default '[]'::jsonb,
  career_goals jsonb not null default '[]'::jsonb,
  price_label text not null default 'Free resource',
  is_external boolean not null default false,
  is_active boolean not null default true,
  vector_sync_status text not null default 'pending'
    check (vector_sync_status in ('pending', 'synced', 'failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists products_category_idx on public.products(category);
create index if not exists products_difficulty_idx on public.products(difficulty);
create index if not exists products_active_idx on public.products(is_active);
create index if not exists products_content_type_idx on public.products(content_type);

alter table public.products enable row level security;

drop policy if exists "Anyone can read active catalog items" on public.products;
create policy "Anyone can read active catalog items"
  on public.products for select
  using (is_active = true);

-- Seed rows are intentionally metadata-first: original summaries and links,
-- never copied third-party course bodies.
insert into public.products (
  slug, title, description, short_summary, content_type, provider, source_url,
  license_info, category, difficulty, duration_minutes, skills, prerequisites,
  learning_outcomes, career_goals, price_label, is_external, vector_sync_status
)
values
(
  'python-foundations-for-builders',
  'Python Foundations for Builders',
  'A practical starting module for people who want to build APIs, data tools, and AI products with Python. Focus on the language habits that make later projects easier to ship.',
  'Build the Python fluency you need before moving into APIs and AI applications.',
  'original_module', 'SkillOrbit', null, 'Original SkillOrbit module',
  'Backend Development', 'Beginner', 240,
  '["Python", "functions", "data structures", "testing"]'::jsonb,
  '[]'::jsonb,
  '["Read and structure small Python programs", "Use functions and collections confidently", "Choose a useful next backend project"]'::jsonb,
  '["AI Engineer", "Backend Developer", "Generative AI Builder"]'::jsonb,
  'Free learning module', false, 'pending'
),
(
  'fastapi-api-foundations',
  'FastAPI API Foundations',
  'An original project-led module for turning Python knowledge into a small, documented API. You will think through routes, validation, errors, and the shape of a useful backend.',
  'Turn Python skills into a clean, documented API.',
  'original_module', 'SkillOrbit', null, 'Original SkillOrbit module',
  'Backend Development', 'Beginner', 300,
  '["FastAPI", "REST APIs", "Pydantic", "HTTP"]'::jsonb,
  '["Python Foundations for Builders"]'::jsonb,
  '["Design a small REST API", "Validate request data", "Return clear errors and status codes"]'::jsonb,
  '["Backend Developer", "AI Engineer", "Production AI Product Builder"]'::jsonb,
  'Free learning module', false, 'pending'
),
(
  'sql-for-product-builders',
  'SQL for Product Builders',
  'Learn the relational thinking behind products that need reliable users, catalog items, events, and recommendations. The module uses realistic product questions rather than isolated syntax drills.',
  'Understand the database questions your product needs to answer.',
  'original_module', 'SkillOrbit', null, 'Original SkillOrbit module',
  'Data Foundations', 'Beginner', 220,
  '["SQL", "PostgreSQL", "data modeling", "joins"]'::jsonb,
  '["Python Foundations for Builders"]'::jsonb,
  '["Model related product data", "Write useful filtering and aggregation queries", "Recognize when a relation belongs in its own table"]'::jsonb,
  '["Backend Developer", "AI Engineer", "Data/ML Foundations"]'::jsonb,
  'Free learning module', false, 'pending'
),
(
  'building-a-first-rag-app',
  'Build Your First RAG Application',
  'A guided project track for understanding retrieval-augmented generation from the product point of view: prepare knowledge, retrieve evidence, and make the answer traceable.',
  'Build a small grounded AI application instead of a generic chatbot.',
  'project', 'SkillOrbit', null, 'Original SkillOrbit project track',
  'Generative AI', 'Intermediate', 420,
  '["RAG", "retrieval", "prompting", "evaluation"]'::jsonb,
  '["FastAPI API Foundations", "Embeddings and Semantic Search"]'::jsonb,
  '["Explain the RAG loop", "Connect retrieved evidence to a generation step", "Design a simple grounded response"]'::jsonb,
  '["AI Engineer", "Generative AI Builder", "Production AI Product Builder"]'::jsonb,
  'Free project track', false, 'pending'
),
(
  'embeddings-and-semantic-search',
  'Embeddings and Semantic Search',
  'A plain-language module explaining how text becomes a searchable vector and how similarity can surface related ideas even when the words differ.',
  'Understand the retrieval layer behind modern AI search.',
  'original_module', 'SkillOrbit', null, 'Original SkillOrbit module',
  'Generative AI', 'Intermediate', 260,
  '["embeddings", "semantic search", "vector similarity", "retrieval"]'::jsonb,
  '["Python Foundations for Builders"]'::jsonb,
  '["Describe what an embedding represents", "Compare lexical and semantic search", "Choose metadata that improves retrieval"]'::jsonb,
  '["AI Engineer", "Generative AI Builder"]'::jsonb,
  'Free learning module', false, 'pending'
),
(
  'production-rag-systems',
  'Production RAG Systems',
  'A senior-level project track about the details that turn a RAG demo into a dependable product: chunking, filters, ranking, citations, evaluation, and failure states.',
  'Move from a RAG demo to a system you can trust.',
  'project', 'SkillOrbit', null, 'Original SkillOrbit project track',
  'Generative AI', 'Advanced', 540,
  '["RAG", "evaluation", "re-ranking", "observability", "grounding"]'::jsonb,
  '["Building a First RAG App", "Embeddings and Semantic Search"]'::jsonb,
  '["Design a retrieval quality checklist", "Handle weak or missing evidence", "Explain why a response is grounded"]'::jsonb,
  '["AI Engineer", "Generative AI Builder", "Production AI Product Builder"]'::jsonb,
  'Free project track', false, 'pending'
),
(
  'agent-workflows-with-tools',
  'Agent Workflows with Tools',
  'A practical module for designing an agent as an explicit workflow: observe, decide, use a tool, validate the result, and choose the next action.',
  'Design agents as reliable workflows, not mysterious prompts.',
  'original_module', 'SkillOrbit', null, 'Original SkillOrbit module',
  'Agentic AI', 'Advanced', 360,
  '["AI agents", "tool calling", "workflows", "state"]'::jsonb,
  '["Building a First RAG App"]'::jsonb,
  '["Break an agent into testable steps", "Define safe tool boundaries", "Add validation before an agent response reaches a user"]'::jsonb,
  '["AI Engineer", "Generative AI Builder", "Production AI Product Builder"]'::jsonb,
  'Free learning module', false, 'pending'
),
(
  'ship-an-ai-product',
  'Ship an AI Product',
  'An end-to-end build track covering the product decisions around an AI feature: user need, data, retrieval, feedback, cost, and a demo that earns trust.',
  'Turn an AI idea into a focused, demo-ready product.',
  'project', 'SkillOrbit', null, 'Original SkillOrbit project track',
  'AI Product Building', 'Advanced', 600,
  '["AI product design", "FastAPI", "RAG", "feedback loops", "deployment"]'::jsonb,
  '["FastAPI API Foundations", "Production RAG Systems"]'::jsonb,
  '["Define an AI feature with a measurable user outcome", "Design a grounded recommendation loop", "Prepare a clear product demo"]'::jsonb,
  '["AI Engineer", "Production AI Product Builder"]'::jsonb,
  'Free project track', false, 'pending'
),
(
  'python-official-tutorial',
  'The Python Tutorial',
  'The official Python documentation path for learning the language fundamentals and standard library. SkillOrbit adds a suggested sequence and keeps the source transparent.',
  'A trustworthy official reference for Python fundamentals.',
  'documentation', 'Python Software Foundation',
  'https://docs.python.org/3/tutorial/',
  'Python documentation license; follow the source terms',
  'Backend Development', 'Beginner', 360,
  '["Python", "syntax", "standard library"]'::jsonb,
  '[]'::jsonb,
  '["Follow the official Python learning sequence", "Use the language reference when building projects"]'::jsonb,
  '["AI Engineer", "Backend Developer", "Data/ML Foundations"]'::jsonb,
  'Free official resource', true, 'pending'
),
(
  'fastapi-official-tutorial',
  'FastAPI Official Tutorial',
  'The official FastAPI tutorial covering path operations, request data, security, databases, and deployment concepts. Use it as a reference while building.',
  'Learn FastAPI from the people who maintain it.',
  'documentation', 'FastAPI',
  'https://fastapi.tiangolo.com/tutorial/',
  'FastAPI documentation license; follow the source terms',
  'Backend Development', 'Intermediate', 420,
  '["FastAPI", "Python", "API design", "OpenAPI"]'::jsonb,
  '["Python Foundations for Builders"]'::jsonb,
  '["Navigate the official FastAPI learning path", "Build typed API endpoints", "Use generated API documentation"]'::jsonb,
  '["Backend Developer", "AI Engineer"]'::jsonb,
  'Free official resource', true, 'pending'
),
(
  'mdn-http-overview',
  'MDN HTTP Overview',
  'A curated entry point into MDN Web Docs for understanding requests, responses, methods, headers, and status codes—the shared language of web products.',
  'Build the web fundamentals behind every API.',
  'documentation', 'MDN Web Docs',
  'https://developer.mozilla.org/en-US/docs/Web/HTTP',
  'MDN content is available under CC-BY-SA; follow attribution terms',
  'Backend Development', 'Beginner', 180,
  '["HTTP", "web fundamentals", "requests", "responses"]'::jsonb,
  '[]'::jsonb,
  '["Read an HTTP exchange", "Choose appropriate methods and status codes", "Debug a request with browser tools"]'::jsonb,
  '["Backend Developer", "AI Engineer", "Production AI Product Builder"]'::jsonb,
  'Free official resource', true, 'pending'
),
(
  'postgresql-official-tutorial',
  'PostgreSQL Tutorial',
  'The official PostgreSQL documentation entry point for relational database concepts, SQL, administration, and working with a real production-grade database.',
  'Use the official PostgreSQL guide to strengthen your data foundation.',
  'documentation', 'PostgreSQL',
  'https://www.postgresql.org/docs/current/tutorial.html',
  'PostgreSQL documentation license; follow the source terms',
  'Data Foundations', 'Intermediate', 300,
  '["PostgreSQL", "SQL", "relational data"]'::jsonb,
  '["SQL for Product Builders"]'::jsonb,
  '["Query a relational database", "Understand tables and constraints", "Use official docs while building"]'::jsonb,
  '["Backend Developer", "Data/ML Foundations", "AI Engineer"]'::jsonb,
  'Free official resource', true, 'pending'
),
(
  'hugging-face-llm-course',
  'Hugging Face LLM Course',
  'An open learning path for modern NLP and language model concepts, including transformer foundations and practical model use.',
  'A structured open course for understanding language models.',
  'external_course', 'Hugging Face',
  'https://huggingface.co/learn/llm-course/chapter1/1',
  'Open course; follow the source terms',
  'Generative AI', 'Intermediate', 900,
  '["transformers", "NLP", "language models", "Hugging Face"]'::jsonb,
  '["Python Foundations for Builders"]'::jsonb,
  '["Understand transformer-based language models", "Use the Hugging Face ecosystem as a learning reference"]'::jsonb,
  '["AI Engineer", "Generative AI Builder", "Data/ML Foundations"]'::jsonb,
  'Free open course', true, 'pending'
),
(
  'qdrant-vector-search-guide',
  'Qdrant Vector Search Guide',
  'The official Qdrant learning and documentation path for collections, payloads, filtering, and vector search—the practical retrieval layer for AI products.',
  'Learn vector search from the vector database maintainers.',
  'documentation', 'Qdrant',
  'https://qdrant.tech/documentation/overview/',
  'Qdrant documentation license; follow the source terms',
  'Generative AI', 'Intermediate', 300,
  '["Qdrant", "vector databases", "semantic search", "metadata filters"]'::jsonb,
  '["Embeddings and Semantic Search"]'::jsonb,
  '["Understand a vector collection", "Use payload filters", "Connect retrieval to an AI feature"]'::jsonb,
  '["AI Engineer", "Generative AI Builder"]'::jsonb,
  'Free official resource', true, 'pending'
),
(
  'supabase-auth-database-guide',
  'Supabase Auth and Database Guide',
  'An official reference path for authentication, Postgres data, row-level security, and building a secure app with Supabase.',
  'Use Supabase confidently without skipping security fundamentals.',
  'documentation', 'Supabase',
  'https://supabase.com/docs/guides/getting-started',
  'Supabase documentation license; follow the source terms',
  'AI Product Building', 'Intermediate', 270,
  '["Supabase", "PostgreSQL", "authentication", "RLS"]'::jsonb,
  '["SQL for Product Builders"]'::jsonb,
  '["Connect auth identity to application data", "Understand row-level security", "Use the Supabase docs to make secure choices"]'::jsonb,
  '["Backend Developer", "Production AI Product Builder", "AI Engineer"]'::jsonb,
  'Free official resource', true, 'pending'
),
(
  'build-a-course-recommendation-agent',
  'Build a Course Recommendation Agent',
  'The SkillOrbit capstone project: combine event tracking, semantic retrieval, and personalized messaging into an agent that explains the next learning step.',
  'Build the same kind of grounded behavioral agent that powers SkillOrbit.',
  'project', 'SkillOrbit', null, 'Original SkillOrbit capstone',
  'Agentic AI', 'Advanced', 720,
  '["behavioral signals", "RAG", "recommendations", "FastAPI", "Qdrant"]'::jsonb,
  '["Production RAG Systems", "Agent Workflows with Tools"]'::jsonb,
  '["Design a behavior-to-recommendation loop", "Ground recommendations in a real catalog", "Explain an AI recommendation to a user"]'::jsonb,
  '["AI Engineer", "Generative AI Builder", "Production AI Product Builder"]'::jsonb,
  'Free capstone project', false, 'pending'
)
on conflict (slug) do nothing;