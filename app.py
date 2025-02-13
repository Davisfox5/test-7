This project will consist of two main components: the front-end, which will be built using React, and the back-end, which will be built using Flask. The front-end will have a button that, when clicked, will send a request to the back-end. The back-end will then generate a random word and send it back to the front-end, where it will be displayed to the user.

Here's how you can structure your project:

### Front-end (React)

You can create a new React app using `create-react-app`.

```bash
npx create-react-app word-generator
```

Then, in your `App.js` file, you can create a button that sends a request to the back-end when clicked.

```jsx
// App.js

import React, { useState } from 'react';
import axios from 'axios';

function App() {
  const [word, setWord] = useState('');

  const getWord = () => {
    axios.get('http://localhost:5000/word')
      .then((response) => {
        setWord(response.data.word);
      });
  };

  return (
    <div className="App">
      <button onClick={getWord}>Generate word</button>
      <p>{word}</p>
    </div>
  );
}

export default App;
```

### Back-end (Flask)

You can create a new Flask app in a file called `app.py`.

```python
# app.py

from flask import Flask, jsonify
from random_word import RandomWords

app = Flask(__name__)
r = RandomWords()

@app.route('/word', methods=['GET'])
def get_word():
    word = r.get_random_word()
    return jsonify({'word': word})

if __name__ == '__main__':
    app.run(debug=True)
```

This Flask app uses the `random-word` package to generate random words. You can install it using pip:

```bash
pip install random-word
```

### Deployment

You can deploy both the front-end and back-end on AWS using Elastic Beanstalk, which supports both Node.js (for the React app) and Python (for the Flask app). You would need to create two separate Elastic Beanstalk environments, one for each app.

### Cost Estimate

The cost of hosting this app on AWS would depend on the resources you use. If you use the free tier, you would not incur any costs. However, once you exceed the free tier limits, you would start to incur costs.

Here's a rough estimate:

- EC2 (for the servers): $0.013 per Hour x 2 (for two servers) x 24 (hours in a day) x 30 (days in a month) = $18.72
- Elastic Beanstalk: No additional charge; you only pay for the underlying AWS resources.
- Data transfer: The first 1 GB is free; beyond that, it's $0.09 per GB.

Please note that this is a rough estimate and the actual cost may vary.

### Summary

This project involves creating a React-based front-end and a Flask-based back-end. The front-end sends a request to the back-end when a button is clicked, and the back-end generates a random word and sends it back to the front-end. The estimated cost of hosting this app on AWS is around $18.72 per month, excluding data transfer costs.