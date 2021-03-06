import numpy as np
import random
import copy
from collections import namedtuple, deque
import torch
import torch.nn as nn
import torch.nn.functional as F

from ddpg_agent import Agent
from memory import ReplayBuffer


buffer_size = int(1e5)             
batch_size = 200                   
update_freq = 4        
ma_update_freq = 3     
gamma = 0.995
tau = 1e-3 


device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

class Maddpg():
    """MADDPG Agent : Interacts with and learns from the environment."""
    
    def __init__(self, state_size, action_size, num_agents, random_seed):
        """Initialize a MADDPG Agent object.
        
        Params
        ======
            state_size (int): dimension of each state
            action_size (int): dimension of each action
            num_agents (int): number of agents
            random_seed (int): random seed
        """
        
        super(Maddpg, self).__init__()
        
        self.state_size = state_size
        self.action_size = action_size
        self.num_agents = num_agents
        self.seed = random.seed(random_seed)
        
        # Instantiate Multiple  Agent
        self.agents = [ Agent(state_size,action_size, random_seed, num_agents) 
                       for i in range(num_agents) ]
        
        # Instantiate Memory replay Buffer (shared between agents)
        self.memory = ReplayBuffer(action_size, buffer_size, batch_size, random_seed)
        
                  
    def reset(self):
        """Reset all the agents"""
        for agent in self.agents:
            agent.reset()

    def act(self, states, noise):
        """Return action to perform for each agents (per policy)"""        
        return [ agent.act(state, noise) for agent, state in zip(self.agents, states) ]
                
    
    def step(self, states, actions, rewards, next_states, dones, num_current_episode):
        """ # Save experience in replay memory, and use random sample from buffer to learn"""
 
        #self.memory.add(states, It mainly reuse function from ``actions, rewards, next_states, dones)
        self.memory.add(np.array(states).reshape(1,-1).squeeze(),
                        np.array(actions).reshape(1,-1).squeeze(),
                        rewards,
                        np.array(next_states).reshape(1,-1).squeeze(),
                        dones)

        # If enough samples in the replay memory and if it is time to update
        if (len(self.memory) > batch_size) and (num_current_episode % update_freq ==0) :
            
            # Note: this code only expects 2 agents
            assert(len(self.agents)==2)
            
            # Allow to learn several time in a row in the same episode
            for i in range(ma_update_freq):
                # Sample a batch of experience from the replay buffer 
                experiences = self.memory.sample()   
                # Update Agent #0
                self.maddpg_learn(experiences, own_idx=0, other_idx=1)
                # Sample another batch of experience from the replay buffer 
                experiences = self.memory.sample()   
                # Update Agent #1
                self.maddpg_learn(experiences, own_idx=1, other_idx=0)
                
    
    def maddpg_learn(self, experiences, own_idx, other_idx, gamma=gamma):
        """
        Update the policy of the MADDPG "own" agent. The actors have only access to agent own 
        information, whereas the critics have access to all agents information.
        
        Update policy and value parameters using given batch of experience tuples.
        Q_targets = r + ?? * critic_target(next_state, actor_target(next_state))
        where:
            actor_target(states) -> action
            critic_target(all_states, all_actions) -> Q-value
        Params
        ======
            experiences (Tuple[torch.Tensor]): tuple of (s, a, r, s', done) tuples 
            own_idx (int) : index of the own agent to update in self.agents
            other_idx (int) : index of the other agent to update in self.agents
            gamma (float): discount factor
        """
        
        states, actions, rewards, next_states, dones = experiences
        
        def filterout(size, num_agents, id_agent, sa):
            list_indices  = torch.tensor([ idx for idx in range(id_agent * size, id_agent * size + size) ]).to(device)    
            out = sa.index_select(1, list_indices)
            return out
        
        # Filter out the agent OWN states, actions and next_states batch
        own_states =  filterout(self.state_size, self.num_agents, own_idx, states)
        own_actions = filterout(self.action_size, self.num_agents, own_idx, actions)
        own_next_states = filterout(self.state_size, self.num_agents, own_idx, next_states) 
                
        # Filter out the OTHER agent states, actions and next_states batch
        other_states =  filterout(self.state_size, self.num_agents, other_idx, states)
        other_actions = filterout(self.action_size, self.num_agents, other_idx, actions)
        other_next_states = filterout(self.state_size, self.num_agents, other_idx, next_states)
        
        # Concatenate both agent information (own agent first, other agent in second position)
        all_states=torch.cat((own_states, other_states), dim=1).to(device)
        all_actions=torch.cat((own_actions, other_actions), dim=1).to(device)
        all_next_states=torch.cat((own_next_states, other_next_states), dim=1).to(device)
   
        agent = self.agents[own_idx]
        
            
        # ---------------------------- update critic ---------------------------- #
        # Get predicted next-state actions and Q values from target models        
        all_next_actions = torch.cat((agent.actor_target(own_states), agent.actor_target(other_states)),
                                     dim =1).to(device) 
        Q_targets_next = agent.critic_target(all_next_states, all_next_actions)
        
        
        # Compute Q targets for current states (y_i)
        Q_targets = rewards + (gamma * Q_targets_next * (1 - dones))
        
        # Compute critic loss
        Q_expected = agent.critic_local(all_states, all_actions)
        critic_loss = F.mse_loss(Q_expected, Q_targets)
        
        # Minimize the loss
        agent.critic_optimizer.zero_grad()
        critic_loss.backward()
        agent.critic_optimizer.step()

        # ---------------------------- update actor ---------------------------- #
        # Compute actor loss
        all_actions_pred = torch.cat((agent.actor_local(own_states), agent.actor_local(other_states).detach()),
                                     dim = 1).to(device)      
        actor_loss = -agent.critic_local(all_states, all_actions_pred).mean()
        
        # Minimize the loss
        agent.actor_optimizer.zero_grad()
        actor_loss.backward()        
        agent.actor_optimizer.step()

        # ----------------------- update target networks ----------------------- #
        agent.soft_update(agent.critic_local, agent.critic_target, tau)
        agent.soft_update(agent.actor_local, agent.actor_target, tau)                   
    
