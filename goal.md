# Overview

We need to build a system for managing income and expense. This system will have multiple layers.
User can chat with an agent and provide image or screenshot of receipt or just tell the details then agent or system will extract relevant deyails from inage or text then update a Google sheet as well show in Ui dashboard.
System will have 2 channels UI and Whatsapp to inetract with system or agent. User can also ask question like
"what is my expneses this month?", "can you categorize the expenses by category?", "What is net incoem this month? and show me the trend of income and expenses over last 6 months?"

System should support generative Ui capability to generate UI on the fly based on user question (I think this will only support on UI if it can be possible on Whatsapp that will be greate)

# Technology Stack
python 3.13+, 
poetry for package management
docker for running app
For UI, react + generative UI like A2UI

# Constraint
1. We have to use Pi agent/core as a agentic frameowkr (https://pi.dev/docs/latest)
2. For agent we can use Claude code MAX subscription I had
3. We need to have Google drive inetgration to sore images and google sheet.
4. For OCR, we can use Nvidia builder models.
5. Backend should be in Python and frontend shouldn't have any business logic everything should come from backend APi 
6. Pi core or agent we should use python SDK not node.
7. Makefile commands to start (backend + console (UI)), stop, cleanup and see logs

# Acceptance criteria

1. User should be able to connect it's whatsapp by scaning bar code on UI.
2. When user provide image or screenshot systen will be able to parse the details like date, amount, GST/QST, store image in Google driver folder and also mention image link in the google sheet along with other data.
3. User can ask question related income and expenses via UUI or Whatsapp
4. User able to able configure categories for expense and income.
5. User can also set formular per actegory like when a receipt ro amount mention for category so system should consider 100% or some custom, default 100%.