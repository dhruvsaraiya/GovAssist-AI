User: mereko mudra loan ka form bharna hein
Assistant UX: gets the input and passes to backend
Assistant Backend: gets the input + adds the list of available form links => sends to model
(TODO: have a prompt that can ask model to do below stuff)
(How does model know the form fields??)
Model : returns appropriate form link + returns a summary of the form in either text/audio format (as however user has started the convo)
Model: asks user for the first field in the form (e.g. "What is your name?") (the both steps can be combined in single response)

User: gives their answer (e.g. "My name is Kishan")
Backend: gets the answer, sends to model
Model: gives a json in specified format
UX: reads the json -> prefills value in the form from that json
UX -> gives ack to backend
Backend -> tells model
Model: prepares a question for user for the next field.
....and continues....

------ FLOW --------------
- first give me a sequence diagram in new file under docs for the whole end to end flow of user communication where 
  - user puts a message in frontend
  - frontend gives it to backend
  - backend along with that message and system prompt asks ai
  - ai sugests the form, gives form summary, and asks user to provide answer for first form field
    - this includes model -> backend -> ui interaction
  - user gives answer
  - backend gives it to model
  - model responds with structured schema and value in json, that is passed to UI, that updates the form
  - frontend gives next field to backend and model
  - model asks next question
  - user answers and flow continues...

